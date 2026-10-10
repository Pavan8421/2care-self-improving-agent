# 2care Self-Improving Scheduling Agent

Weekend take-home: a **text** multi-turn patient appointment agent (LangChain / LangGraph) with a hybrid evaluation harness and a closed **improvement loop** that only commits prompt patches after a full green shadow suite.

## Status

Phases **0–7** complete. See [docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md) for exit criteria.

## Architecture (short)

```text
Patient (web UI or LLM simulator)
        │
        ▼
 LangGraph: agent_node ↔ tools_node
   prompts: system.md + improvements.md
        │
        ▼
 Tools + code guards → in-memory clinic store (policy, slot re-check)
```

- **Dialogue** = LLM. **Truth** = store + tools. Guards block book without verify / soft-confirm / relationship.
- **Eval** scores DB + tool_trace + safety (not transcript-only).
- **Improve** proposes a patch → shadow-tests up to 3 times in memory → writes disk **only if all scenarios pass**.

## Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set a real OPENAI_API_KEY in .env
```

Always run Python modules from `backend/` (so `app.*` / `eval.*` import cleanly). Root scripts do that for you.

## Two main commands

```bash
# 1) Manual chat UI + API
./scripts/run_agent.sh
# → http://127.0.0.1:8000/

# 2) Eval suite (optionally improve)
./scripts/run_eval.sh
./scripts/run_eval.sh --improve --propose   # diagnose only
./scripts/run_eval.sh --improve --apply     # ≤3 shadow trials; commit if all-green
```

Demo patients in the fake clinic: **Alice Nguyen** `1990-03-12`, **Bob Martinez** `1985-07-04`, **Carol Singh** `1978-11-22`.

Artifacts: `backend/eval/results/latest.json`, trials under `backend/eval/results/trials/`, audits under `backend/improvements/`.

## Layout

```text
backend/     agent, clinic store, tools, FastAPI, eval
frontend/    tiny web chat
docs/        DESIGN.md, IMPLEMENTATION_PLAN.md, DESIGN_NOTE_1P.md
scripts/     run_agent.sh, run_eval.sh
```

## Docs

- [Design document](docs/DESIGN.md) — agent design, tools, eval, **improvement-loop design**
- [1-page design note](docs/DESIGN_NOTE_1P.md) — short summary for submission
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — phases and exit criteria

## AI vs judgment

| AI helped with | Human judgment |
|---|---|
| Scaffolding, glue code, draft prompts | Scenario set and what “pass” means |
| LLM patient / agent dialogue | Rubric weights (DB/tools over transcript) |
| Optional LLM patch revisions | Guards, 24h policy, third-party verify rules |
| | Planted race gap + shadow-then-commit policy |

We do **not** trust a polite transcript alone: store + tool_trace are the source of truth.

## Other useful commands

```bash
cd backend && source .venv/bin/activate
python scripts/smoke_phase0.py   # imports / settings
python scripts/smoke_phase3.py   # agent wiring (+ live if key set)
python scripts/smoke_phase4.py   # API + static UI
python -m app.agent.cli          # terminal chat
python -m eval.harness -v        # verbose suite
python -m eval.harness --only slot_taken_race
```
