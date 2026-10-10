# 2care Self-Improving Scheduling Agent

Weekend take-home: a patient appointment scheduling agent (LangChain / LangGraph) with an evaluation harness that improves from failed runs.

## Status

- Scaffold + design: done  
- **Phase 0 (environment):** done  
- **Phase 1 (clinic store + policy):** done  
- **Phase 2 (tools + guards):** done  
- **Phase 3 (LangGraph agent + prompts):** done  
- **Phase 4 (FastAPI + tiny web UI):** done  
- **Phase 5 (eval harness + rubric):** done  
- **Phase 6 (improvement loop):** done  
- Phase 7: not started  

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
python -m app.main          # API + UI on APP_HOST:APP_PORT (default 8000)
python -m eval.harness      # later: eval loop
python scripts/smoke_phase0.py
python scripts/smoke_phase4.py
```

Or from repo root: `./scripts/run_agent.sh` then open http://127.0.0.1:8000/

## Docs

- [Design document](docs/DESIGN.md) — agent, tools, eval harness, improvement loop, assumptions
- [Implementation plan](docs/IMPLEMENTATION_PLAN.md) — phase-by-phase build order and exit criteria

## Commands

```bash
./scripts/run_agent.sh   # chat UI + API → http://127.0.0.1:8000/
./scripts/run_eval.sh    # score scenarios → backend/eval/results/latest.json
./scripts/run_eval.sh --improve --propose   # diagnose only (no disk writes)
./scripts/run_eval.sh --improve --apply     # up to 3 shadow trials; commit only if all-green
# Shadow trials use in-memory prompts (disk unchanged until a trial passes 7/7).
# Trial logs: backend/eval/results/trials/trial_00N.json
# or: cd backend && python -m eval.harness -v
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
python scripts/smoke_phase3.py   # structure always; live multi-turn if key set
python -m app.agent.cli          # interactive scheduling chat (needs real key)
```

Expected smoke output includes `smoke_imports=ok`. If the key is still a placeholder you will see `key_present=no` — set `OPENAI_API_KEY` in `backend/.env` before agent/eval work.
