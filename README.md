# 2care Self-Improving Scheduling Agent

Weekend take-home: a patient appointment scheduling agent (LangChain / LangGraph) with an evaluation harness that improves from failed runs.

## Status

- Scaffold + design: done  
- **Phase 0 (environment):** done  
- **Phase 1 (clinic store + policy):** done  
- **Phase 2 (tools + guards):** done  
- Phases 3–7: not started  

## Quick layout

```text
backend/     LangGraph agent, clinic store, tools, FastAPI, eval harness
frontend/    Tiny web chat UI (manual testing)
docs/        Design + implementation plan
scripts/     One-command runners (agent, eval loop)
data/        Optional runtime fixtures
```

## How to run Python modules

Treat **`backend/` as the working directory** (it is on `sys.path` for `app.*` and `eval.*`):

```bash
cd backend
source .venv/bin/activate
python -m app.main          # later: API + UI
python -m eval.harness      # later: eval loop
python scripts/smoke_phase0.py
```

Root `scripts/run_agent.sh` / `run_eval.sh` will `cd` into `backend` the same way.

## Docs

- [Design document](docs/DESIGN.md) — agent, tools, eval harness, improvement loop, assumptions
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — phase-by-phase build order and exit criteria

## Intended commands (after later phases)

```bash
./scripts/run_agent.sh   # chat UI + API
./scripts/run_eval.sh    # score scenarios, apply improvement, re-run
```

## Setup (Phase 0)

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Edit .env and set a real OPENAI_API_KEY (required from Phase 3 onward)

python scripts/smoke_phase0.py
```

Expected smoke output includes `smoke_imports=ok`. If the key is still a placeholder you will see `key_present=no` — set `OPENAI_API_KEY` in `backend/.env` before agent/eval work.
