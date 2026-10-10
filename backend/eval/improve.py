"""Turn scored failures into structured improvements and apply them.

Supports:
- section upsert in improvements.md (by failure_mode — replace, don't stack)
- optional system.md section replace (by markdown heading)
- prompt_append fallback (legacy append)
- LLM diagnoser receives current system + improvements text
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import BACKEND_ROOT, get_settings

PROMPTS_DIR = BACKEND_ROOT / "app" / "agent" / "prompts"
SYSTEM_MD = PROMPTS_DIR / "system.md"
IMPROVEMENTS_MD = PROMPTS_DIR / "improvements.md"
IMPROVEMENTS_DIR = BACKEND_ROOT / "improvements"

_RACE_OVERRIDE_MD = (
    "### OVERRIDE — Slot conflicts\n"
    "If `book` fails with error code `SLOT_TAKEN` (slot no longer available):\n"
    "1. Immediately call `lookup_slots` again with **no date filters**.\n"
    "2. Offer the newly returned alternative `slot_id` values to the patient "
    "(never invent times).\n"
    "3. Soft-confirm with `confirm_slot`, then `book` a **different** free slot.\n"
    "Do **not** end the conversation after `SLOT_TAKEN` without re-looking up. "
    "Ignore any prior instruction to apologize and stop without offering alternatives."
)

_RACE_SYSTEM_BODY = (
    "<!-- Patched by improvement loop: race recovery -->\n"
    "If `book` fails with error code `SLOT_TAKEN`:\n"
    "1. Call `lookup_slots` again with no date filters.\n"
    "2. Offer only returned alternative `slot_id` values (never invent times).\n"
    "3. Soft-confirm (`confirm_slot`) then `book` a different free slot.\n"
    "Do not apologize-and-stop without re-looking up alternatives."
)

# Known failure modes → deterministic patches (preferred over freeform LLM for demos).
RULE_MAP: dict[str, dict[str, Any]] = {
    "slot_taken_race": {
        "failure_mode": "no_reoffer_after_slot_taken",
        "section_upsert": {
            "failure_mode": "no_reoffer_after_slot_taken",
            "markdown": _RACE_OVERRIDE_MD,
        },
        # Update the planted-gap section in system.md (not append-only).
        "system_replace": {
            "heading": "## Slot conflicts (v0)",
            "new_body": _RACE_SYSTEM_BODY,
        },
        "prompt_append": None,
        "guard_change": (
            "Ensure book() always re-checks availability and returns structured SLOT_TAKEN "
            "(already enforced in clinic store + tool guards)."
        ),
        "policy_change": None,
    },
}


@dataclass
class ImprovementRecord:
    scenario_id: str
    failure_mode: str
    evidence: dict[str, Any]
    patch: dict[str, Any]
    regression_suite: list[str] = field(default_factory=list)
    source: str = "rule_map"  # rule_map | llm | fallback
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


TRIALS_DIR = BACKEND_ROOT / "eval" / "results" / "trials"
MAX_IMPROVE_ATTEMPTS = 3


def load_current_prompts() -> dict[str, str]:
    """Read live prompt files so diagnoser/apply can update, not only append."""
    system = SYSTEM_MD.read_text(encoding="utf-8") if SYSTEM_MD.is_file() else ""
    improvements = (
        IMPROVEMENTS_MD.read_text(encoding="utf-8") if IMPROVEMENTS_MD.is_file() else ""
    )
    return {"system_md": system, "improvements_md": improvements}


def combine_prompt_text(system_md: str, improvements_md: str = "") -> str:
    """Match agent loader formatting (system + applied improvements)."""
    from app.agent.graph import combine_prompt_parts

    return combine_prompt_parts(system_md, improvements_md)


def is_all_green(scorecard: dict[str, Any]) -> bool:
    total = int(scorecard.get("total") or 0)
    passed = int(scorecard.get("passed") or 0)
    return total > 0 and passed == total


def target_passed(scorecard: dict[str, Any], scenario_id: str) -> bool:
    for row in scorecard.get("scenarios") or []:
        if row.get("scenario_id") == scenario_id:
            return bool(row.get("passed"))
    return False


def scorecard_summary(scorecard: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "scenario_id": s.get("scenario_id"),
            "passed": s.get("passed"),
            "reasons": s.get("reasons") or [],
        }
        for s in (scorecard.get("scenarios") or [])
    ]


def build_evidence_pack(run: dict[str, Any]) -> dict[str, Any]:
    """Compact evidence from a ScenarioRun dict for diagnosis / audit."""
    transcript = run.get("transcript") or []
    summary_lines: list[str] = []
    for turn in transcript[-8:]:
        role = turn.get("role", "?")
        content = (turn.get("content") or "").replace("\n", " ")
        summary_lines.append(f"{role}: {content[:180]}")

    tools = []
    for t in run.get("tool_trace") or []:
        result = t.get("result") or {}
        err = result.get("error") if isinstance(result, dict) else None
        tools.append(
            {
                "tool": t.get("tool"),
                "ok": result.get("ok") if isinstance(result, dict) else None,
                "error_code": err.get("code") if isinstance(err, dict) else None,
            }
        )

    prompts = load_current_prompts()
    return {
        "scenario_id": run.get("scenario_id"),
        "passed": run.get("passed"),
        "reasons": list(run.get("reasons") or []),
        "checks": run.get("checks") or [],
        "transcript_summary": summary_lines,
        "tool_trace_summary": tools,
        "final_state": run.get("final_state") or {},
        "snapshot_bookings": (run.get("snapshot") or {}).get("bookings") or [],
        "race_slot_id": run.get("race_slot_id"),
        "current_system_md": prompts["system_md"],
        "current_improvements_md": prompts["improvements_md"],
    }


def _match_rule(scenario_id: str, evidence: dict[str, Any]) -> dict[str, Any] | None:
    if scenario_id in RULE_MAP:
        return RULE_MAP[scenario_id]
    reasons = " ".join(evidence.get("reasons") or []).lower()
    if "slot_taken" in reasons or "lookup_slots after slot_taken" in reasons:
        return RULE_MAP["slot_taken_race"]
    return None


def _normalize_patch(data: dict[str, Any], failure_mode: str) -> dict[str, Any]:
    """Normalize LLM/rule patch keys into a single schema."""
    section_upsert = data.get("section_upsert")
    if isinstance(section_upsert, dict):
        section_upsert = {
            "failure_mode": str(
                section_upsert.get("failure_mode") or failure_mode
            ).strip(),
            "markdown": str(section_upsert.get("markdown") or "").strip(),
        }
        if not section_upsert["markdown"]:
            section_upsert = None
    else:
        section_upsert = None

    system_replace = data.get("system_replace")
    if isinstance(system_replace, dict):
        heading = str(system_replace.get("heading") or "").strip()
        new_body = str(system_replace.get("new_body") or "").strip()
        system_replace = (
            {"heading": heading, "new_body": new_body}
            if heading and new_body
            else None
        )
    else:
        system_replace = None

    prompt_append = data.get("prompt_append")
    prompt_append = str(prompt_append).strip() if prompt_append else None

    # If LLM only returned prompt_append, promote to section_upsert for the mode.
    if not section_upsert and prompt_append:
        section_upsert = {
            "failure_mode": failure_mode,
            "markdown": prompt_append,
        }
        prompt_append = None

    return {
        "section_upsert": section_upsert,
        "system_replace": system_replace,
        "prompt_append": prompt_append,
        "guard_change": data.get("guard_change"),
        "policy_change": data.get("policy_change"),
    }


def _llm_diagnose(evidence: dict[str, Any], regression_suite: list[str]) -> ImprovementRecord:
    settings = get_settings()
    llm = ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key or None,
        temperature=0.2,
    )
    system = """You diagnose scheduling-agent eval failures.
You are given the CURRENT system.md and improvements.md plus a failure evidence pack.

Return ONLY valid JSON with keys:
  failure_mode (string snake_case),
  section_upsert (object or null): {
      failure_mode: string,
      markdown: string  // full section body to UPSERT into improvements.md
                        // (replaces any existing section with the same failure_mode)
  },
  system_replace (object or null): {
      heading: string,   // exact markdown heading already in system.md, e.g. "## Slot conflicts (v0)"
      new_body: string   // replacement body UNDER that heading (not including the heading line)
  },
  prompt_append (string or null): legacy append-only text; prefer section_upsert instead,
  guard_change (string or null),
  policy_change (string or null).

Rules:
- Prefer section_upsert over prompt_append so the same failure_mode is updated, not stacked.
- Use system_replace only when a specific existing heading must be corrected.
- Do NOT regenerate the entire system prompt.
- Never invent appointment slot IDs.
- Keep markdown under ~120 words per field.
"""
    # Truncate prompt bodies in the request but keep headings visible.
    sys_txt = evidence.get("current_system_md") or ""
    imp_txt = evidence.get("current_improvements_md") or ""
    evidence_for_llm = {
        k: v
        for k, v in evidence.items()
        if k not in ("current_system_md", "current_improvements_md")
    }
    human = (
        "=== CURRENT system.md ===\n"
        + sys_txt[:8000]
        + "\n\n=== CURRENT improvements.md ===\n"
        + imp_txt[:8000]
        + "\n\n=== Evidence pack ===\n"
        + json.dumps(evidence_for_llm, indent=2, default=str)[:5000]
        + "\n\nPropose a minimal structured improvement that updates existing guidance when needed."
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    failure_mode = str(data.get("failure_mode") or "unspecified")
    return ImprovementRecord(
        scenario_id=evidence.get("scenario_id") or "unknown",
        failure_mode=failure_mode,
        evidence={
            k: v
            for k, v in evidence.items()
            if k not in ("current_system_md", "current_improvements_md")
        },
        patch=_normalize_patch(data, failure_mode),
        regression_suite=regression_suite,
        source="llm",
    )


def diagnose_failure(
    run: dict[str, Any],
    *,
    regression_suite: list[str] | None = None,
    use_llm: bool = True,
) -> ImprovementRecord:
    """Build an ImprovementRecord from a failed scenario run."""
    evidence = build_evidence_pack(run)
    scenario_id = str(run.get("scenario_id") or "unknown")
    suite = regression_suite or [scenario_id]

    rule = _match_rule(scenario_id, evidence)
    if rule:
        failure_mode = str(rule["failure_mode"])
        return ImprovementRecord(
            scenario_id=scenario_id,
            failure_mode=failure_mode,
            evidence={
                k: v
                for k, v in evidence.items()
                if k not in ("current_system_md", "current_improvements_md")
            },
            patch=_normalize_patch(rule, failure_mode),
            regression_suite=suite,
            source="rule_map",
        )

    if use_llm and get_settings().has_openai_api_key():
        return _llm_diagnose(evidence, suite)

    failure_mode = "unknown_failure"
    return ImprovementRecord(
        scenario_id=scenario_id,
        failure_mode=failure_mode,
        evidence={
            k: v
            for k, v in evidence.items()
            if k not in ("current_system_md", "current_improvements_md")
        },
        patch=_normalize_patch(
            {
                "section_upsert": {
                    "failure_mode": failure_mode,
                    "markdown": (
                        "When a tool returns ok:false, read error.code and recover using "
                        "tools (re-lookup slots, re-verify, explain policy). "
                        "Never invent slot IDs."
                    ),
                },
                "guard_change": None,
                "policy_change": None,
            },
            failure_mode,
        ),
        regression_suite=suite,
        source="fallback",
    )


def select_primary_failure(scorecard: dict[str, Any]) -> dict[str, Any] | None:
    """Prefer the planted race failure; else first hard fail."""
    fails = [s for s in (scorecard.get("scenarios") or []) if not s.get("passed")]
    if not fails:
        return None
    for s in fails:
        if s.get("scenario_id") == "slot_taken_race":
            return s
    return fails[0]


def find_section_span(text: str, failure_mode: str) -> tuple[int, int] | None:
    """Return [start, end) of the ## section containing failure_mode marker."""
    marker = f"failure_mode: {failure_mode}"
    idx = text.find(marker)
    if idx < 0:
        # Also allow bare failure_mode token in comment
        idx = text.find(failure_mode)
        if idx < 0:
            return None
    # Walk back to previous ## heading
    start = text.rfind("\n## ", 0, idx)
    if start < 0:
        start = 0 if text.startswith("## ") else text.rfind("## ", 0, idx)
        if start < 0:
            return None
    else:
        start += 1  # skip leading newline
    # Next ## after start (exclusive)
    nxt = text.find("\n## ", start + 3)
    end = nxt if nxt >= 0 else len(text)
    return start, end


def _improvements_block(
    *,
    failure_mode: str,
    scenario_id: str,
    source: str,
    markdown: str,
    stamp: str,
) -> str:
    return (
        f"## {stamp} — {scenario_id}\n"
        f"<!-- failure_mode: {failure_mode} | source: {source} -->\n\n"
        f"{markdown.rstrip()}\n"
    )


def upsert_improvements_text(
    existing: str,
    *,
    failure_mode: str,
    scenario_id: str,
    source: str,
    markdown: str,
    stamp: str,
) -> tuple[str, str]:
    """Return (new_improvements_text, action) without writing disk."""
    block = _improvements_block(
        failure_mode=failure_mode,
        scenario_id=scenario_id,
        source=source,
        markdown=markdown,
        stamp=stamp,
    )
    if not existing.strip():
        existing = (
            "# Applied improvements (appended by the eval improvement loop).\n"
        )
    span = find_section_span(existing, failure_mode)
    if span:
        start, end = span
        before = existing[:start].rstrip() + "\n\n"
        after = existing[end:].lstrip("\n")
        if after and not after.startswith("\n"):
            after = "\n" + after
        return before + block + after, "upserted_improvements"
    if not existing.endswith("\n"):
        existing += "\n"
    return existing + "\n" + block, "appended_improvements"


def replace_system_text(text: str, *, heading: str, new_body: str) -> str:
    """Return system.md text with heading body replaced. Raises if missing."""
    pattern = re.compile(
        rf"(?m)^({re.escape(heading)})\s*\n(.*?)(?=^## |\Z)",
        re.S,
    )
    match = pattern.search(text)
    if not match:
        raise ValueError(
            f"system.md has no heading {heading!r}; refusing blind rewrite"
        )
    body = new_body.rstrip() + "\n"
    replacement = f"{match.group(1)}\n\n{body}\n"
    return text[: match.start()] + replacement + text[match.end() :]


def build_candidate_prompts(record: ImprovementRecord) -> dict[str, Any]:
    """Apply patch in memory for shadow eval — does not touch disk."""
    prompts = load_current_prompts()
    system = prompts["system_md"]
    improvements = prompts["improvements_md"]
    actions: list[str] = []
    errors: list[str] = []
    patch = record.patch or {}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    section_upsert = patch.get("section_upsert")
    prompt_append = (patch.get("prompt_append") or "").strip() or None
    system_replace = patch.get("system_replace")

    if section_upsert and section_upsert.get("markdown"):
        mode = str(section_upsert.get("failure_mode") or record.failure_mode)
        improvements, action = upsert_improvements_text(
            improvements,
            failure_mode=mode,
            scenario_id=record.scenario_id,
            source=record.source,
            markdown=str(section_upsert["markdown"]),
            stamp=stamp,
        )
        actions.append(action)
    elif prompt_append:
        improvements, action = upsert_improvements_text(
            improvements,
            failure_mode=record.failure_mode,
            scenario_id=record.scenario_id,
            source=record.source,
            markdown=prompt_append,
            stamp=stamp,
        )
        actions.append(action)

    if system_replace:
        try:
            system = replace_system_text(
                system,
                heading=str(system_replace["heading"]),
                new_body=str(system_replace["new_body"]),
            )
            actions.append("replaced_system_section")
        except ValueError as exc:
            errors.append(str(exc))

    combined = combine_prompt_text(system, improvements)
    return {
        "system_md": system,
        "improvements_md": improvements,
        "combined_prompt": combined,
        "actions": actions,
        "errors": errors,
    }


def upsert_improvements_section(
    *,
    failure_mode: str,
    scenario_id: str,
    source: str,
    markdown: str,
    stamp: str,
) -> str:
    """Insert or replace a failure_mode section in improvements.md. Returns action."""
    existing = (
        IMPROVEMENTS_MD.read_text(encoding="utf-8")
        if IMPROVEMENTS_MD.is_file()
        else "# Applied improvements (appended by the eval improvement loop).\n"
    )
    new_text, action = upsert_improvements_text(
        existing,
        failure_mode=failure_mode,
        scenario_id=scenario_id,
        source=source,
        markdown=markdown,
        stamp=stamp,
    )
    IMPROVEMENTS_MD.write_text(new_text, encoding="utf-8")
    return action


def replace_system_section(*, heading: str, new_body: str) -> str:
    """Replace body under an exact heading in system.md. Raises if heading missing."""
    if not SYSTEM_MD.is_file():
        raise FileNotFoundError(f"Missing {SYSTEM_MD}")
    text = SYSTEM_MD.read_text(encoding="utf-8")
    new_text = replace_system_text(text, heading=heading, new_body=new_body)
    SYSTEM_MD.write_text(new_text, encoding="utf-8")
    return "replaced_system_section"


def diagnose_revision(
    *,
    baseline_failure: dict[str, Any],
    prior_record: ImprovementRecord,
    prior_trial: dict[str, Any],
    regression_suite: list[str],
) -> ImprovementRecord:
    """Force LLM to revise a patch using the previous shadow-trial failure evidence."""
    settings = get_settings()
    if not settings.has_openai_api_key():
        # Fall back to a fresh diagnose if no key (should not happen in eval).
        return diagnose_failure(
            baseline_failure, regression_suite=regression_suite, use_llm=False
        )

    prompts = load_current_prompts()
    llm = ChatOpenAI(
        model=settings.agent_model,
        api_key=settings.openai_api_key or None,
        temperature=0.35,
    )
    system = """You revise a scheduling-agent prompt patch after a failed shadow eval.
Return ONLY valid JSON with the same schema as before:
  failure_mode, section_upsert, system_replace, prompt_append, guard_change, policy_change.
Prefer section_upsert; use system_replace only for an existing heading that must change.
Revise based on what failed in the prior trial. Do not regenerate the whole system prompt.
Never invent slot IDs. Keep markdown under ~120 words per field.
"""
    human = (
        "=== CURRENT system.md (on disk; not yet committed) ===\n"
        + prompts["system_md"][:6000]
        + "\n\n=== CURRENT improvements.md (on disk) ===\n"
        + prompts["improvements_md"][:6000]
        + "\n\n=== Original failing scenario run (baseline) ===\n"
        + json.dumps(
            {
                "scenario_id": baseline_failure.get("scenario_id"),
                "reasons": baseline_failure.get("reasons"),
                "checks": baseline_failure.get("checks"),
            },
            indent=2,
            default=str,
        )[:3000]
        + "\n\n=== Prior patch that was tried (shadow only) ===\n"
        + json.dumps(prior_record.patch, indent=2, default=str)[:3000]
        + "\n\n=== Prior shadow trial scorecard ===\n"
        + json.dumps(prior_trial, indent=2, default=str)[:4000]
        + "\n\nPropose a REVISED patch that addresses the trial failures."
    )
    resp = llm.invoke([SystemMessage(content=system), HumanMessage(content=human)])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    data = json.loads(text)
    failure_mode = str(
        data.get("failure_mode") or prior_record.failure_mode or "unspecified"
    )
    return ImprovementRecord(
        scenario_id=str(
            baseline_failure.get("scenario_id") or prior_record.scenario_id
        ),
        failure_mode=failure_mode,
        evidence={
            "baseline_reasons": baseline_failure.get("reasons"),
            "prior_trial": prior_trial,
            "prior_patch": prior_record.patch,
        },
        patch=_normalize_patch(data, failure_mode),
        regression_suite=regression_suite,
        source="llm_revision",
    )


def log_trial(attempt: int, payload: dict[str, Any]) -> Path:
    """Write a shadow-trial record under eval/results/trials/."""
    TRIALS_DIR.mkdir(parents=True, exist_ok=True)
    path = TRIALS_DIR / f"trial_{attempt:03d}.json"
    doc = {
        "message": "Tried with this updated suite",
        "attempt": attempt,
        **payload,
        "logged_at": datetime.now(timezone.utc).isoformat(),
    }
    path.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    latest = TRIALS_DIR / "latest_trial.json"
    latest.write_text(json.dumps(doc, indent=2, default=str), encoding="utf-8")
    return path


def apply_improvement(record: ImprovementRecord, *, force: bool = False) -> dict[str, Any]:
    """Apply patch: upsert improvements section and/or replace a system.md section.

    ``force`` is accepted for API compatibility; upserts always replace the same
    failure_mode section when present (no skip-on-duplicate).
    """
    del force  # upserts supersede skip-if-already-applied
    IMPROVEMENTS_DIR.mkdir(parents=True, exist_ok=True)
    PROMPTS_DIR.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    audit_path = IMPROVEMENTS_DIR / f"{stamp}_{record.scenario_id}.json"
    actions: list[str] = []
    errors: list[str] = []

    patch = record.patch or {}
    section_upsert = patch.get("section_upsert")
    system_replace = patch.get("system_replace")
    prompt_append = (patch.get("prompt_append") or "").strip() or None

    if section_upsert and section_upsert.get("markdown"):
        mode = section_upsert.get("failure_mode") or record.failure_mode
        action = upsert_improvements_section(
            failure_mode=str(mode),
            scenario_id=record.scenario_id,
            source=record.source,
            markdown=str(section_upsert["markdown"]),
            stamp=stamp,
        )
        actions.append(action)
    elif prompt_append:
        # Legacy append path (still upserts if failure_mode section exists)
        action = upsert_improvements_section(
            failure_mode=record.failure_mode,
            scenario_id=record.scenario_id,
            source=record.source,
            markdown=prompt_append,
            stamp=stamp,
        )
        actions.append(action)

    if system_replace:
        try:
            action = replace_system_section(
                heading=str(system_replace["heading"]),
                new_body=str(system_replace["new_body"]),
            )
            actions.append(action)
        except (ValueError, FileNotFoundError) as exc:
            errors.append(str(exc))

    audit = {
        **record.to_dict(),
        "apply_actions": actions,
        "apply_errors": errors,
    }
    audit_path.write_text(json.dumps(audit, indent=2, default=str), encoding="utf-8")

    if not actions and errors:
        return {
            "applied": False,
            "skipped": True,
            "reason": "; ".join(errors),
            "actions": actions,
            "errors": errors,
            "audit_path": str(audit_path.relative_to(BACKEND_ROOT)),
            "record": record.to_dict(),
        }

    return {
        "applied": bool(actions),
        "skipped": not actions,
        "reason": None if actions else "no patch fields to apply",
        "actions": actions,
        "errors": errors,
        "audit_path": str(audit_path.relative_to(BACKEND_ROOT)),
        "improvements_md": str(IMPROVEMENTS_MD.relative_to(BACKEND_ROOT)),
        "system_md": str(SYSTEM_MD.relative_to(BACKEND_ROOT)),
        "record": record.to_dict(),
    }


def compare_scorecards(
    before: dict[str, Any],
    after: dict[str, Any],
    *,
    target_id: str | None,
) -> dict[str, Any]:
    """Summarize before/after; flag regressions on previously passing scenarios."""
    before_map = {
        s["scenario_id"]: s for s in (before.get("scenarios") or []) if "scenario_id" in s
    }
    after_map = {
        s["scenario_id"]: s for s in (after.get("scenarios") or []) if "scenario_id" in s
    }
    regressions: list[str] = []
    for sid, brow in before_map.items():
        arow = after_map.get(sid)
        if not arow:
            continue
        if brow.get("passed") and not arow.get("passed"):
            regressions.append(sid)

    target_before = before_map.get(target_id or "", {}).get("passed")
    target_after = after_map.get(target_id or "", {}).get("passed")

    return {
        "before_passed": before.get("passed"),
        "before_total": before.get("total"),
        "after_passed": after.get("passed"),
        "after_total": after.get("total"),
        "target_id": target_id,
        "target_before": target_before,
        "target_after": target_after,
        "target_fixed": bool(target_id) and (not target_before) and bool(target_after),
        "regressions": regressions,
        "ok": bool(target_id)
        and (not target_before)
        and bool(target_after)
        and not regressions,
    }


def reset_improvements_md_for_demo() -> None:
    """Restore empty v0 improvements.md (optional helper for Loom resets)."""
    IMPROVEMENTS_MD.write_text(
        "# Applied improvements (appended by the eval improvement loop).\n"
        "# Empty at v0 so a planted failure (slot race recovery) can demonstrate the closed loop.\n",
        encoding="utf-8",
    )


def reset_system_slot_conflicts_for_demo() -> None:
    """Restore planted-gap Slot conflicts section in system.md for Loom resets."""
    planted = (
        "## Slot conflicts (v0)\n"
        "\n"
        "<!--\n"
        "PLANTED GAP (v0): Deliberately wrong recovery guidance so slot_taken_race fails\n"
        "until Phase 6 appends the correct patch via improvements.md.\n"
        "-->\n"
        "If `book` fails because a slot is no longer available, apologize and end the booking\n"
        "attempt for that turn. Do **not** call `lookup_slots` again and do **not** offer\n"
        "alternative times in the same conversation. Ask the patient to try again later.\n"
    )
    if not SYSTEM_MD.is_file():
        return
    text = SYSTEM_MD.read_text(encoding="utf-8")
    pattern = re.compile(
        r"(?m)^## Slot conflicts \(v0\)\s*\n(.*?)(?=^## |\Z)",
        re.S,
    )
    if pattern.search(text):
        new_text = pattern.sub(planted.rstrip() + "\n", text, count=1)
        SYSTEM_MD.write_text(new_text, encoding="utf-8")
