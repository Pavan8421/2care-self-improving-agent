"""Eval runner: scenarios → agent runs → rubric scores → optional improve."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import typer
import yaml
from rich.console import Console
from rich.table import Table

from app.agent.graph import SchedulingSession
from app.clinic.store import ClinicStore
from app.config import BACKEND_ROOT, get_settings
from eval.improve import (
    MAX_IMPROVE_ATTEMPTS,
    ImprovementRecord,
    apply_improvement,
    build_candidate_prompts,
    diagnose_failure,
    diagnose_revision,
    is_all_green,
    log_trial,
    scorecard_summary,
    select_primary_failure,
    target_passed,
)
from eval.patient_simulator import DONE_TOKEN, PatientSimulator
from eval.rubric import ScoreResult, score_scenario

EVAL_DIR = Path(__file__).resolve().parent
SCENARIOS_PATH = EVAL_DIR / "scenarios" / "scenarios.yaml"
RESULTS_DIR = EVAL_DIR / "results"

app = typer.Typer(add_completion=False, no_args_is_help=False)
console = Console()


@dataclass
class ScenarioRun:
    scenario_id: str
    passed: bool
    hard_failed: bool
    reasons: list[str]
    turns: int
    race_slot_id: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    final_state: dict[str, Any] = field(default_factory=dict)
    snapshot: dict[str, Any] = field(default_factory=dict)
    checks: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_scenarios(path: Path | None = None) -> list[dict[str, Any]]:
    raw = yaml.safe_load((path or SCENARIOS_PATH).read_text(encoding="utf-8")) or {}
    scenarios = raw.get("scenarios") or []
    if not isinstance(scenarios, list):
        raise ValueError("scenarios.yaml must contain a list under 'scenarios'")
    return scenarios


def _parse_now(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _apply_setup(store: ClinicStore, setup: dict[str, Any]) -> None:
    now = _parse_now(setup.get("now"))
    store.reset(now=now)
    for pre in setup.get("prebook") or []:
        result = store.book(
            slot_id=pre["slot_id"],
            patient_id=pre["patient_id"],
            relationship=pre.get("relationship") or "self",
        )
        if not result.get("ok"):
            raise RuntimeError(f"prebook failed: {result}")


class _RaceAwareStore(ClinicStore):
    """Marks the first offered slot taken immediately after lookup returns.

    Runs inside the tool call so a same-turn ``book`` of that slot hits
    ``SLOT_TAKEN`` (post-turn hooks are too late for single-turn book flows).
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.race_enabled = False
        self.race_slot_id: str | None = None
        self._race_fired = False

    def lookup_slots(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        free_only: bool = True,
    ) -> dict[str, Any]:
        result = super().lookup_slots(date_from, date_to, free_only=free_only)
        if (
            self.race_enabled
            and not self._race_fired
            and result.get("ok")
            and result.get("slots")
        ):
            slot_id = result["slots"][0].get("slot_id")
            if slot_id:
                # Result payload still lists the slot as free; store marks it taken
                # before any subsequent book in this tools batch.
                self.mark_slot_taken(slot_id)
                self.race_slot_id = slot_id
                self._race_fired = True
        return result


def _build_store(setup: dict[str, Any]) -> ClinicStore:
    hooks = setup.get("hooks") or []
    wants_race = any(
        h.get("when") == "after_lookup_slots"
        and h.get("action") == "mark_first_offered_slot_taken"
        for h in hooks
    )
    store: ClinicStore = _RaceAwareStore() if wants_race else ClinicStore()
    _apply_setup(store, setup)
    if wants_race and isinstance(store, _RaceAwareStore):
        store.race_enabled = True
    return store


def run_scenario(
    scenario: dict[str, Any],
    *,
    simulator: PatientSimulator | None = None,
    verbose: bool = False,
    prompt_override: str | None = None,
) -> ScenarioRun:
    scenario_id = scenario["id"]
    setup = scenario.get("setup") or {}
    patient = scenario.get("patient") or {}
    expect = scenario.get("expect") or {}
    max_turns = int(scenario.get("max_turns") or 10)

    store = _build_store(setup)
    session = SchedulingSession(
        store=store,
        session_id=f"eval-{scenario_id}",
        prompt_override=prompt_override,
    )
    sim = simulator or PatientSimulator()

    transcript: list[dict[str, str]] = []
    all_tools: list[dict[str, Any]] = []
    final_state: dict[str, Any] = {}
    turns = 0

    try:
        opening = sim.opening(patient)
        patient_msg: str | None = opening

        while turns < max_turns and patient_msg:
            if PatientSimulator.is_done(patient_msg):
                break

            transcript.append({"role": "patient", "content": patient_msg})
            if verbose:
                console.print(f"[cyan]Patient:[/cyan] {patient_msg}")

            turn = session.invoke(patient_msg)
            turns += 1
            final_state = turn.state
            delta = list(turn.tool_trace or [])
            # tool_trace on TurnResult is cumulative via reducer — take only new
            # entries relative to what we already stored.
            if len(delta) >= len(all_tools) and delta[: len(all_tools)] == all_tools:
                new_tools = delta[len(all_tools) :]
            else:
                # Fallback: use delta as this-turn only if shapes diverge
                new_tools = delta
            all_tools.extend(new_tools)

            agent_reply = turn.reply or ""
            transcript.append({"role": "agent", "content": agent_reply})
            if verbose:
                console.print(f"[green]Agent:[/green] {agent_reply[:500]}")
                if isinstance(store, _RaceAwareStore) and store.race_slot_id:
                    console.print(
                        f"[yellow]hook:[/yellow] race slot "
                        f"{store.race_slot_id} marked taken after lookup"
                    )

            nxt = sim.next_message(
                scenario_id=scenario_id,
                patient=patient,
                transcript=transcript,
            )
            if PatientSimulator.is_done(nxt):
                if verbose:
                    console.print(f"[dim]Patient: {DONE_TOKEN}[/dim]")
                break
            patient_msg = nxt

        snapshot = store.snapshot()
        race_slot_id = (
            store.race_slot_id if isinstance(store, _RaceAwareStore) else None
        )
        # Prefer full cumulative tool list from final state if present
        trace = list(final_state.get("tool_trace") or all_tools)
        score: ScoreResult = score_scenario(
            scenario_id=scenario_id,
            expect=expect,
            snapshot=snapshot,
            tool_trace=trace,
            transcript=transcript,
            state=final_state,
            race_slot_id=race_slot_id,
        )
        return ScenarioRun(
            scenario_id=scenario_id,
            passed=score.passed,
            hard_failed=score.hard_failed,
            reasons=score.reasons,
            turns=turns,
            race_slot_id=race_slot_id,
            transcript=transcript,
            tool_trace=trace,
            final_state={
                k: final_state.get(k)
                for k in (
                    "verified_patient_id",
                    "relationship",
                    "booking_id",
                    "escalated",
                    "safety_flags",
                    "soft_confirmed",
                )
            },
            snapshot={
                "now": snapshot.get("now"),
                "bookings": snapshot.get("bookings"),
                "free_slot_ids": [
                    s.get("slot_id") for s in (snapshot.get("free_slots") or [])
                ],
            },
            checks=score.to_dict()["checks"],
        )
    except Exception as exc:  # noqa: BLE001
        return ScenarioRun(
            scenario_id=scenario_id,
            passed=False,
            hard_failed=True,
            reasons=[f"runtime error: {exc}"],
            turns=turns,
            transcript=transcript,
            tool_trace=all_tools,
            error=str(exc),
        )


def run_suite(
    *,
    only: list[str] | None = None,
    verbose: bool = False,
    scenarios_path: Path | None = None,
    prompt_override: str | None = None,
    label: str | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    if not settings.has_openai_api_key():
        raise RuntimeError(
            "OPENAI_API_KEY missing or placeholder — set backend/.env before eval."
        )

    scenarios = load_scenarios(scenarios_path)
    if only:
        want = set(only)
        scenarios = [s for s in scenarios if s.get("id") in want]
        missing = want - {s.get("id") for s in scenarios}
        if missing:
            raise ValueError(f"Unknown scenario ids: {sorted(missing)}")

    simulator = PatientSimulator()
    runs: list[ScenarioRun] = []
    for scenario in scenarios:
        title = scenario.get("id")
        if label:
            title = f"{label} / {title}"
        console.rule(f"[bold]{title}[/bold]")
        result = run_scenario(
            scenario,
            simulator=simulator,
            verbose=verbose,
            prompt_override=prompt_override,
        )
        runs.append(result)
        status = "[green]PASS[/green]" if result.passed else "[red]FAIL[/red]"
        console.print(f"{status}  turns={result.turns}")
        for reason in result.reasons[:6]:
            console.print(f"  • {reason}")

    passed = sum(1 for r in runs if r.passed)
    total = len(runs)
    scorecard = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "agent_model": settings.agent_model,
        "patient_model": settings.patient_model,
        "passed": passed,
        "total": total,
        "prompt_override": bool(prompt_override),
        "label": label,
        "scenarios": [r.to_dict() for r in runs],
    }
    return scorecard


def print_scorecard_table(scorecard: dict[str, Any]) -> None:
    table = Table(title=f"Eval scorecard ({scorecard['passed']}/{scorecard['total']})")
    table.add_column("Scenario")
    table.add_column("Result")
    table.add_column("Turns", justify="right")
    table.add_column("Top reason")
    for row in scorecard.get("scenarios") or []:
        table.add_row(
            row.get("scenario_id", "?"),
            "PASS" if row.get("passed") else "FAIL",
            str(row.get("turns", 0)),
            (row.get("reasons") or ["—"])[0][:80],
        )
    console.print(table)


def save_scorecard(scorecard: dict[str, Any], path: Path | None = None) -> Path:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = path or RESULTS_DIR / f"scorecard_{stamp}.json"
    # Also write latest.json for easy inspection
    out.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")
    latest = RESULTS_DIR / "latest.json"
    latest.write_text(json.dumps(scorecard, indent=2, default=str), encoding="utf-8")
    return out


def _print_proposal(record: ImprovementRecord) -> None:
    console.rule("[bold]Improvement proposal[/bold]")
    console.print(f"scenario_id: {record.scenario_id}")
    console.print(f"failure_mode: {record.failure_mode}")
    console.print(f"source: {record.source}")
    su = record.patch.get("section_upsert") or {}
    sr = record.patch.get("system_replace") or {}
    console.print(f"section_upsert: {bool(su.get('markdown'))}")
    if su.get("markdown"):
        console.print(su.get("markdown"))
    console.print(f"system_replace: {sr.get('heading') if sr else None}")
    if record.patch.get("guard_change"):
        console.print(f"guard_change: {record.patch['guard_change']}")


def _run_improve_loop(
    *,
    before: dict[str, Any],
    apply: bool,
    verbose: bool,
    scenarios_path: Path | None,
    only: list[str] | None,
) -> int:
    """Shadow-trial improve loop: up to 3 attempts; commit only if all-green.

    Real prompt files are untouched until a shadow suite is fully green.
    """
    primary = select_primary_failure(before)
    if primary is None:
        console.print("[green]No failures — nothing to improve.[/green]")
        return 0

    suite_ids = [s.get("scenario_id") for s in (before.get("scenarios") or [])]
    suite_ids = [s for s in suite_ids if s]
    target_id = str(primary.get("scenario_id") or "")

    record = diagnose_failure(primary, regression_suite=suite_ids, use_llm=True)
    _print_proposal(record)

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    propose_path = RESULTS_DIR / "last_proposal.json"
    propose_path.write_text(
        json.dumps(record.to_dict(), indent=2, default=str),
        encoding="utf-8",
    )
    console.print(f"Wrote proposal {propose_path.relative_to(BACKEND_ROOT)}")

    if not apply:
        console.print(
            "[yellow]Dry-run (--propose). Use --improve --apply for up to "
            f"{MAX_IMPROVE_ATTEMPTS} shadow trials; commit only if all-green.[/yellow]"
        )
        return 0

    trials_log: list[dict[str, Any]] = []
    committed: dict[str, Any] | None = None

    for attempt in range(1, MAX_IMPROVE_ATTEMPTS + 1):
        console.rule(f"[bold]Shadow trial {attempt}/{MAX_IMPROVE_ATTEMPTS}[/bold]")
        if attempt > 1:
            _print_proposal(record)

        candidate = build_candidate_prompts(record)
        if candidate.get("errors"):
            console.print(f"[yellow]Candidate warnings:[/yellow] {candidate['errors']}")
        if not candidate.get("combined_prompt"):
            console.print("[red]Empty candidate prompt — aborting attempt.[/red]")
            decision = "give_up"
            trial_path = log_trial(
                attempt,
                {
                    "decision": decision,
                    "target_id": target_id,
                    "patch": record.patch,
                    "candidate_actions": candidate.get("actions"),
                    "errors": candidate.get("errors"),
                    "scorecard": None,
                },
            )
            trials_log.append({"attempt": attempt, "decision": decision, "path": str(trial_path)})
            break

        console.print(
            f"[dim]Shadow suite with in-memory prompts "
            f"(actions={candidate.get('actions')}; disk unchanged)[/dim]"
        )
        trial = run_suite(
            only=only,
            verbose=verbose,
            scenarios_path=scenarios_path,
            prompt_override=candidate["combined_prompt"],
            label=f"trial-{attempt}",
        )
        print_scorecard_table(trial)
        all_green = is_all_green(trial)
        tgt_ok = target_passed(trial, target_id)

        if all_green:
            decision = "commit"
        elif tgt_ok:
            decision = "retry_flake"
        else:
            decision = "retry_revise"

        trial_path = log_trial(
            attempt,
            {
                "decision": decision,
                "target_id": target_id,
                "target_passed": tgt_ok,
                "all_green": all_green,
                "patch": record.patch,
                "source": record.source,
                "candidate_actions": candidate.get("actions"),
                "candidate_errors": candidate.get("errors"),
                "scorecard": {
                    "passed": trial.get("passed"),
                    "total": trial.get("total"),
                    "scenarios": scorecard_summary(trial),
                },
            },
        )
        console.print(
            f"Trial {attempt}: {trial.get('passed')}/{trial.get('total')} "
            f"target_passed={tgt_ok} → [bold]{decision}[/bold]"
        )
        console.print(f"Logged {trial_path.relative_to(BACKEND_ROOT)}")
        trials_log.append(
            {
                "attempt": attempt,
                "decision": decision,
                "all_green": all_green,
                "target_passed": tgt_ok,
                "path": str(trial_path.relative_to(BACKEND_ROOT)),
            }
        )

        if decision == "commit":
            console.rule("[bold]Commit to disk (all-green)[/bold]")
            committed = apply_improvement(record)
            if committed.get("skipped"):
                console.print(f"[yellow]Commit skipped:[/yellow] {committed.get('reason')}")
            else:
                console.print(f"[green]Committed[/green] actions={committed.get('actions')}")
                console.print(f"improvements → {committed.get('improvements_md')}")
                console.print(f"system → {committed.get('system_md')}")
                console.print(f"Audit → {committed.get('audit_path')}")
            break

        if attempt >= MAX_IMPROVE_ATTEMPTS:
            console.print(
                f"[red]Gave up after {MAX_IMPROVE_ATTEMPTS} shadow trials; "
                "real prompts left unchanged.[/red]"
            )
            break

        # Prepare next attempt
        if decision == "retry_flake":
            console.print(
                "[yellow]Target passed but suite not all-green (likely flake). "
                "Revising lightly with LLM using trial evidence…[/yellow]"
            )
        else:
            console.print(
                "[yellow]Target still failing. Forcing LLM revision from trial evidence…[/yellow]"
            )
        prior_trial_summary = {
            "passed": trial.get("passed"),
            "total": trial.get("total"),
            "scenarios": scorecard_summary(trial),
            "decision_hint": decision,
        }
        record = diagnose_revision(
            baseline_failure=primary,
            prior_record=record,
            prior_trial=prior_trial_summary,
            regression_suite=suite_ids,
        )

    loop_path = RESULTS_DIR / "improve_loop.json"
    loop_doc = {
        "mode": "shadow_trial_then_commit",
        "max_attempts": MAX_IMPROVE_ATTEMPTS,
        "target_id": target_id,
        "baseline": {
            "passed": before.get("passed"),
            "total": before.get("total"),
            "scenarios": scorecard_summary(before),
        },
        "trials": trials_log,
        "committed": committed,
        "success": bool(committed and committed.get("applied")),
    }
    loop_path.write_text(json.dumps(loop_doc, indent=2, default=str), encoding="utf-8")
    console.print(f"Wrote {loop_path.relative_to(BACKEND_ROOT)}")

    if committed and committed.get("applied"):
        console.print(
            "[green]Closed loop OK: shadow suite all-green; prompts committed.[/green]"
        )
        return 0
    console.print(
        "[red]Closed loop did not commit "
        "(no all-green shadow trial; disk prompts unchanged).[/red]"
    )
    return 1


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    only: list[str] = typer.Option(
        None,
        "--only",
        help="Run only these scenario ids (repeatable).",
    ),
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Print dialogue."),
    scenarios_file: Path = typer.Option(
        SCENARIOS_PATH,
        "--scenarios",
        help="Path to scenarios.yaml",
        exists=True,
        dir_okay=False,
        readable=True,
    ),
    improve: bool = typer.Option(
        False,
        "--improve",
        help="After the suite, diagnose the primary failure (prefer slot_taken_race).",
    ),
    propose: bool = typer.Option(
        False,
        "--propose",
        help="With --improve: dry-run diagnosis only (default when --apply is omitted).",
    ),
    apply: bool = typer.Option(
        False,
        "--apply",
        help=(
            "With --improve: up to 3 shadow trials (in-memory prompts); "
            "commit to disk only if a trial is all-green."
        ),
    ),
) -> None:
    """Run the evaluation suite and write a JSON scorecard under eval/results/."""
    if ctx.invoked_subcommand is not None:
        return
    try:
        scorecard = run_suite(only=only or None, verbose=verbose, scenarios_path=scenarios_file)
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]Harness error:[/red] {exc}")
        raise typer.Exit(code=2) from exc

    print_scorecard_table(scorecard)
    path = save_scorecard(scorecard)
    console.print(f"Wrote {path.relative_to(BACKEND_ROOT)}")
    console.print(f"Also { (RESULTS_DIR / 'latest.json').relative_to(BACKEND_ROOT) }")

    if scorecard["total"] == 0:
        raise typer.Exit(code=1)

    if improve:
        # --propose is the dry-run; --apply writes. If neither, treat as propose.
        do_apply = bool(apply)
        if propose and apply:
            console.print("[yellow]Both --propose and --apply set; applying.[/yellow]")
        code = _run_improve_loop(
            before=scorecard,
            apply=do_apply,
            verbose=verbose,
            scenarios_path=scenarios_file,
            only=only or None,
        )
        raise typer.Exit(code=code)


if __name__ == "__main__":
    # Allow `python -m eval.harness` and `python eval/harness.py`
    app()
