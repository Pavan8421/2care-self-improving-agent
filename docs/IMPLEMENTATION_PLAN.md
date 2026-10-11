# Implementation Plan — Phase by Phase

**Goal:** Ship a runnable scheduling agent, eval harness, and one closed improvement loop within a weekend ceiling (~6–8h).  
**Source of truth for behavior:** [DESIGN.md](./DESIGN.md).  
**Overall status:** Phases 0–7 done.

Each phase lists **status**, **what we work on**, **files touched**, **exit criteria**, and a rough time box. Do not start the next phase until the exit criteria pass. Update **Status** to `Done` only when that phase’s exit criteria are met.

---

## Overview

| Phase | Focus | Approx. | Status |
|---|---|---|---|
| 0 | Environment & project wiring | 20–30 min | Done |
| 1 | Clinic store + policy (fake backend) | 45–60 min | Done |
| 2 | Tools + code guards | 45–60 min | Done |
| 3 | LangGraph agent + prompts | 60–90 min | Done |
| 4 | FastAPI + tiny web UI (manual chat) | 45–60 min | Done |
| 5 | Eval harness + rubric + scenarios | 60–90 min | Done |
| 6 | Improvement loop (diagnose → apply → re-run) | 45–60 min | Done |
| 7 | One-command scripts, polish, demo prep | 30–45 min | Not done |

**Critical path:** 1 → 2 → 3 → 5 → 6 (loop).  
**Parallelizable later:** Phase 4 UI can follow Phase 3; Phase 7 last.

---

## Phase 0 — Environment & wiring

**Status:** Done

### What we work on
- Python venv, install deps from `requirements.txt`
- Copy `.env.example` → `.env`, set `OPENAI_API_KEY`
- Confirm package imports (`langchain`, `langgraph`, `fastapi`)
- Decide how backend is run as a module (`PYTHONPATH` / `backend` as root)

### Files
- `backend/.venv/` (local)
- `backend/.env` (local, gitignored; copy from `.env.example`)
- `backend/app/config.py` — pydantic-settings loader
- `backend/scripts/smoke_phase0.py` — import + settings smoke check
- `backend/requirements.txt`
- `README.md` — setup + run-from-`backend/` convention
- `scripts/run_*.sh` — activate venv + `cd backend`

### Exit criteria
- [x] `pip install -r requirements.txt` succeeds
- [x] Smoke import of OpenAI + LangGraph (+ FastAPI) works via `python scripts/smoke_phase0.py`
- [x] Run convention locked: always `cd backend` (then `python -m app…` / `python -m eval…`)
- [ ] Real `OPENAI_API_KEY` in `backend/.env` — **user action** before Phase 3+ (smoke reports `key_present=no` until set)

---

## Phase 1 — Clinic store + policy

**Status:** Done

### What we work on
- In-memory **patients**, **slots**, **bookings**
- Seed data for demo/eval (2–3 patients, a week of slots)
- Operations: get patient by name+DOB, list free slots, book, get booking, cancel, reschedule
- **24h cancel policy** helper
- Store **reset** for eval isolation (fresh world per scenario)
- Optional: helper to mark a slot taken mid-conversation (for race scenario)

### Files
- `backend/app/clinic/store.py` — seed, CRUD-ish ops, `mark_slot_taken`, `reset`, `default_store`
- `backend/app/clinic/policy.py` — `can_cancel_or_reschedule` (24h notice)
- `backend/app/clinic/__init__.py`
- `backend/scripts/smoke_phase1.py`

### Exit criteria
- [x] Can verify a patient by name + DOB
- [x] Can book a free slot; second book of same slot fails (`SLOT_TAKEN`)
- [x] Cancel inside 24h is rejected by policy; outside is allowed
- [x] `reset()` clears bookings (and restores seed slots) for a clean eval run
- [x] Verified via `python scripts/smoke_phase1.py`

---

## Phase 2 — Tools + code guards

**Status:** Done

### What we work on
- Wrap store operations as **LangChain tools**
- Implement **guards** that reject illegal commits even if the LLM asks:
  - patient must be verified
  - soft confirm required before `book`
  - relationship required (`self` or note)
  - `book` re-checks slot availability → structured `SLOT_TAKEN` error
  - cancel/reschedule respect 24h policy
- `escalate(reason)` tool for emergency / handoff
- Structured tool error payloads the agent can recover from

### Files
- `backend/app/agent/tools/guards.py` — `AgentToolContext` + guard helpers
- `backend/app/agent/tools/clinic_tools.py` — `build_clinic_tools` / `build_default_tools`
- `backend/app/agent/tools/__init__.py`
- `backend/scripts/smoke_phase2.py`

### Tools to ship
`get_patient`, `lookup_slots`, `book`, `get_booking`, `check_policy`, `cancel`, `reschedule`, `escalate`

### Exit criteria
- [x] Calling `book` without verify/soft-confirm fails in code
- [x] Stale slot returns a clear error (`SLOT_TAKEN`)
- [x] Tools are callable from a small manual script / REPL without the full graph
- [x] Verified via `python scripts/smoke_phase2.py`

---

## Phase 3 — LangGraph agent + prompts

**Status:** Done

### What we work on
- Typed **session / graph state** (messages, verified patient, relationship, pending slot, soft_confirmed, booking_id, safety_flags, tool_trace)
- LangGraph: `agent_node` ↔ `tools_node` loop
- Load `system.md` + `improvements.md` into system context
- Dialogue policy in prompt: identify → relationship → intent → clarify → offer → soft confirm → commit → close
- Safety copy: medical refuse; emergency escalate and stop booking
- Session management for multi-turn (thread/session id)
- `confirm_slot` tool bridges dialogue soft-confirm → `AgentToolContext` gates

### Files
- `backend/app/agent/state.py`
- `backend/app/agent/graph.py`
- `backend/app/agent/cli.py` — interactive multi-turn chat
- `backend/app/agent/prompts/system.md`
- `backend/app/agent/prompts/improvements.md` (empty at v0 for planted gap)
- `backend/app/agent/tools/clinic_tools.py` — `confirm_slot` added
- `backend/scripts/smoke_phase3.py`

### Deliberate v0 gap (for loop demo later)
- Leave race recovery **weak in prompt** and/or temporarily soft in one path so `slot_taken_race` can fail once, then get patched in Phase 6  
- Document the planted gap in a short comment or design note so the Loom story is honest
- Planted gap documented in an HTML comment inside `system.md` (no auto re-lookup on `SLOT_TAKEN`)

### Exit criteria
- [x] Multi-turn CLI or notebook chat can verify patient and book with soft confirm (`python -m app.agent.cli`; live path in `smoke_phase3.py` when `OPENAI_API_KEY` is set)
- [x] Agent only offers slots returned by tools (prompt + `lookup_slots` / state `candidate_slots`)
- [x] Third-party booking asks for / records relationship and verifies **attendee** name+DOB (prompt + `book(relationship=…)`)
- [x] Medical / emergency paths behave per DESIGN.md (refuse / `escalate` + `safety_flags`)

---

## Phase 4 — FastAPI + tiny web UI

**Status:** Done

### What we work on
- FastAPI app: health check + `POST /api/chat` (session_id, message → assistant reply + optional state debug)
- Serve `frontend/` as static files
- Wire UI: send message, append replies, keep session id
- Same LangGraph graph as eval will use (one agent entrypoint)
- `POST /api/session/reset` for clean new conversations
- `scripts/run_agent.sh` starts uvicorn

### Files
- `backend/app/main.py`
- `backend/app/api/chat.py`
- `frontend/index.html`
- `frontend/src/main.js`
- `frontend/src/styles.css`
- `backend/scripts/smoke_phase4.py`
- `scripts/run_agent.sh`

### Exit criteria
- [x] Open UI in browser, complete a happy-path book (`./scripts/run_agent.sh` → http://127.0.0.1:8000/)
- [x] New session resets conversation state cleanly (`New session` → new session id)
- [x] Errors from tools surface as normal assistant recovery, not a crashed API (tool errors in-graph; unexpected exceptions return a reply JSON)

---

## Phase 5 — Eval harness + rubric + scenarios

**Status:** Done

### What we work on
- Author **5–7 scenarios** in YAML (goals, patient persona, store setup, expect DB/tools/safety)
- **LLM patient simulator** driven by scenario goals/constraints
- Harness runner: for each scenario → reset store → run dialogue → collect artifacts
- **Hybrid rubric:**
  1. DB truth (primary)
  2. Tool contract (primary)
  3. Safety hard-fail
  4. Optional LLM judge (low weight, never overrides hard fail)
- Scorecard output (console + JSON under `backend/eval/results/`)
- Race hook: after first `lookup_slots`, mark first offered slot taken (`slot_taken_race`)

### Files
- `backend/eval/scenarios/scenarios.yaml`
- `backend/eval/patient_simulator.py`
- `backend/eval/rubric.py`
- `backend/eval/harness.py`
- `backend/eval/results/` — JSON scorecards (`latest.json`)
- `scripts/run_eval.sh`

### Initial scenarios
1. `happy_path_book`  
2. `ambiguous_time`  
3. `slot_taken_race`  
4. `third_party_with_relationship`  
5. `cancel_inside_24h`  
6. `medical_advice_refuse`  
7. `emergency_escalate`

### Exit criteria
- [x] `python -m eval.harness` (or equivalent) runs the full suite
- [x] Scorecard shows pass/fail + reasons per scenario
- [x] At least one hard scenario fails on v0 (planted gap) **or** is ready to fail before Phase 6 patch (`slot_taken_race` fails: v0 prompt tells agent not to re-lookup after `SLOT_TAKEN`)
- [x] Happy path can pass without the improvement patch

---

## Phase 6 — Improvement loop

**Status:** Done

### What we work on
- On failure: build evidence pack (scenario id, transcript summary, tool_trace, DB expect vs actual, rubric reasons)
- **Diagnoser (LLM):** propose structured Improvement JSON — `failure_mode`, `prompt_append`, optional `guard_change` / `policy_change`
- Optional **rule map** for known modes (e.g. race → always ensure `book` re-check guard)
- **Apply:** append to `improvements.md`; write audit JSON under `backend/improvements/`; apply guard/policy toggles if needed
- Flags: `--propose` (dry-run) vs `--apply` (up to 3 shadow trials; commit only if all-green)
- Shadow trials use in-memory prompt override (disk untouched until success)
- Retries 2–3 force LLM revision from prior trial evidence; target-pass + suite-fail treated as flake retry
- Trial logs under `eval/results/trials/`

### Files
- `backend/eval/improve.py`
- `backend/app/agent/prompts/improvements.md`
- `backend/improvements/*.json`
- Hook from `harness.py` (`--improve`)
- `backend/eval/results/improve_loop.json` — trial summary after `--apply`
- `backend/eval/results/trials/trial_00N.json` — each shadow attempt

### Exit criteria
- [x] One documented closed loop: fail → diagnose → shadow trial(s) → commit only if all-green (`--improve --apply`)
- [x] Disk prompts unchanged when no trial is all-green
- [x] Improvement / trial records inspectable on disk (JSON + optional prompt commit)

---

## Phase 7 — Scripts, docs polish

**Status:** Done

### What we work on
- Finalize `scripts/run_agent.sh` and `scripts/run_eval.sh` (true one-command runners)
- Update `README.md`: setup, two commands, **phase-by-phase** explanation, architecture
- 1-page design note (`docs/DESIGN_NOTE_1P.md`) + expand `DESIGN.md` agent + improvement-loop design
- “AI vs judgment” note in README / design note
- No separate demo script (Loom can follow README commands + Design §7)

### Files
- `scripts/run_agent.sh`
- `scripts/run_eval.sh`
- `README.md`
- `docs/DESIGN_NOTE_1P.md`
- `docs/DESIGN.md` (agent + shadow-then-commit improvement design)

### Exit criteria
- [x] Two README commands work from a clean shell with `.env` set
- [x] Before/after / trial artifacts under `eval/results/` and `improvements/`
- [x] README explains each phase; DESIGN.md explains agent + improvement-loop design
- [x] No `DEMO_SCRIPT.md`

---

## Dependency graph

```text
Phase 0
   ↓
Phase 1 (store/policy)
   ↓
Phase 2 (tools/guards)
   ↓
Phase 3 (LangGraph agent)
   ├──────────────→ Phase 4 (UI)      [can overlap with 5]
   ↓
Phase 5 (eval + rubric)
   ↓
Phase 6 (improve loop)
   ↓
Phase 7 (polish + docs)
```

---

## Tracking

Keep this table in sync with the **Status** line under each phase. Values: `Done` or `Not done`. Mark `Done` only when that phase’s exit criteria are met.

| Phase | Status |
|---|---|
| 0 Environment | Done |
| 1 Clinic store + policy | Done |
| 2 Tools + guards | Done |
| 3 LangGraph agent | Done |
| 4 API + web UI | Done |
| 5 Eval harness | Done |
| 6 Improvement loop | Done |
| 7 Scripts + demo polish | Not done |
