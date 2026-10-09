# Design Document — Self-Improving Appointment Scheduling Agent

**Scope:** weekend take-home (6–8h ceiling).  
**Stack:** Python, LangChain, LangGraph, OpenAI, FastAPI, tiny web UI.  
**Status:** design locked; implementation follows this doc.

---

## 1. Goal

Build a **text** multi-turn patient appointment scheduling agent that uses tools to do real work, an **evaluation harness** that scores hard scenarios (not only happy path), and an **improvement loop** that turns a failure into a structured fix, re-runs the suite, and shows the score move **without regressing** what already passed.

We are not building voice, a real EHR, or a large multi-clinic product. Judgment over surface area.

---

## 2. Assumptions (explicit)

| Assumption | Choice |
|---|---|
| Interface | Text chat (tiny web UI + same graph for eval) |
| Clinic | One clinic, one provider |
| Language | English |
| Calendar / PHI | Fake in-memory store; no real patient data |
| Identity | Verify the **appointment patient** by **name + DOB** |
| Third-party booking | Allowed if a **relationship note** is captured (e.g. spouse, parent); booker schedules for the verified attendee |
| Soft confirm | Required before every `book` |
| Cancel policy | Hard **24-hour** window |
| Slot race | On stale slot: tool fails → agent **auto re-looks up** and offers alternatives |
| Medical advice | Brief refuse; stay on scheduling |
| Emergency | Fixed escalate script + `escalate` tool; stop booking that turn |
| Eval patient | LLM-as-patient for harness; humans use the web UI |
| Models | OpenAI (`AGENT_MODEL`, `PATIENT_MODEL`) |
| Improvement apply | Append prompt/policy **and** harden tool guards in code |

If real clinics differ (authorization for proxies, local emergency numbers), that is out of scope; noted here as a deliberate simplification.

---

## 3. Repository structure

```text
backend/
  app/
    main.py                 # FastAPI: API + static UI
    api/chat.py             # Chat HTTP surface
    agent/
      graph.py              # LangGraph orchestration
      state.py              # Typed session / graph state
      prompts/
        system.md           # Base system prompt
        improvements.md     # Patches appended by the loop
      tools/
        clinic_tools.py     # LangChain tools
        guards.py           # Code-level enforcement
    clinic/
      store.py              # In-memory patients / slots / bookings
      policy.py             # 24h cancel, etc.
  eval/
    harness.py              # Run scenarios, score, optionally improve
    rubric.py               # Hybrid scoring
    patient_simulator.py    # LLM patient
    improve.py              # Structured failure → patch → apply
    scenarios/scenarios.yaml
  improvements/             # JSON records of applied patches (audit)
  requirements.txt
  .env.example
frontend/                   # Tiny manual chat UI
docs/DESIGN.md              # This document
scripts/run_agent.sh
scripts/run_eval.sh
data/                       # Optional fixtures
```

---

## 4. Agent design

### 4.1 Role

The LLM is the **dialogue layer**. Truth lives in the clinic store and tools. The orchestrator keeps **explicit session state** and enforces gates so the model cannot silently skip verification, soft confirm, or policy.

### 4.2 Architecture

```text
Patient (web UI or LLM simulator)
        │
        ▼
┌─────────────────────────────────────┐
│  LangGraph                          │
│  agent_node ↔ tools_node (loop)     │
│  + session state + prompt/rules     │
└─────────────────┬───────────────────┘
                  │
                  ▼
┌─────────────────────────────────────┐
│  Tools + guards → clinic store      │
│  policy (24h), slot re-check, etc.  │
└─────────────────────────────────────┘
```

### 4.3 LangGraph shape

Minimal graph (avoid sprawling FSMs):

1. **`agent_node`** — OpenAI chat model with bound tools; reads `system.md` + `improvements.md` + state summary.  
2. **`tools_node`** — Executes tool calls through guards.  
3. Loop until the model responds without tool calls (or escalate ends the productive path).

Optional thin pre-commit checks live **inside tool guards**, not as many graph nodes, so the weekend graph stays readable.

### 4.4 Session state

Maintained in graph state (and returned to the client for debugging/eval):

| Field | Purpose |
|---|---|
| `messages` | Conversation history |
| `patient_id` / `verified` | Appointment patient after name+DOB match |
| `relationship` | `"self"` or note (spouse, parent, …) |
| `intent` | `book` \| `cancel` \| `reschedule` \| `info` |
| `constraints` | Date/time preferences collected so far |
| `candidate_slots` | Last `lookup_slots` results |
| `pending_slot_id` | Slot under discussion |
| `soft_confirmed` | Patient explicitly confirmed that slot |
| `booking_id` | Active booking if any |
| `safety_flags` | e.g. `medical_advice`, `emergency` |
| `tool_trace` | Calls + results for scoring |

### 4.5 Dialogue policy (phases)

1. **Identify** — Collect name + DOB of who will attend; `get_patient`.  
2. **Relationship** — If booker ≠ attendee wording, require a short relationship note before booking.  
3. **Intent** — Book / cancel / reschedule.  
4. **Constraints** — Clarify vague times (“sometime next week”) before lookup.  
5. **Offer** — `lookup_slots`; present only returned slots.  
6. **Soft confirm** — “Shall I book Tuesday 3:00 PM?”  
7. **Commit** — `book` / `cancel` / `reschedule`.  
8. **Close** — Read back tool-confirmed details.

### 4.6 Safety (#6)

| Signal | Behavior |
|---|---|
| Medical advice (dose, diagnosis, “is this normal?”) | Refuse briefly; offer to continue scheduling; set `safety_flags` |
| Emergency (e.g. chest pain, can’t breathe, severe bleeding, suicidal) | Advise contacting emergency services; call `escalate`; **do not** continue into booking that turn |

Eval **hard-fails** if the agent gives clinical advice or books through an emergency turn.

### 4.7 Prompt layout

- `system.md` — role, phases, never invent slots/IDs, soft confirm, third-party rule, safety.  
- `improvements.md` — dated patches from the eval loop, injected with the system prompt.

Prompt states the rules; **tools enforce** them.

---

## 5. Tools

All tools read/write the fake clinic store. Narrow surface to reduce guessing.

| Tool | Responsibility | Guardrails |
|---|---|---|
| `get_patient(name, dob)` | Resolve attendee; mark verified on match | No fuzzy “close enough” book without match |
| `lookup_slots(date_from, date_to, …)` | Return free slots only | Never invent times |
| `book(slot_id, patient_id, relationship)` | Create booking | Require `verified`, `soft_confirmed`, relationship present; **re-check slot free** inside tool; on conflict return structured error |
| `get_booking(booking_id \| patient_id)` | Fetch existing appointment | — |
| `check_policy(action, booking_id, …)` | e.g. cancel within 24h? | Source of policy truth |
| `cancel(booking_id)` | Cancel if policy allows | Must own booking for verified patient; policy enforced in code |
| `reschedule(booking_id, new_slot_id)` | Move appointment | Same verify + soft confirm + slot re-check |
| `escalate(reason)` | Hand off emergency / abuse / admin | Ends productive scheduling for that path |

### 5.1 Code guards (`guards.py`)

Examples enforced regardless of what the LLM says:

- Reject `book` if patient not verified.  
- Reject `book` if `soft_confirmed` is false for that `slot_id`.  
- Reject `book` if slot no longer free (race).  
- Reject cancel/reschedule outside 24h policy (unless a future admin override tool exists — not in v0).  
- Require non-empty `relationship` (`self` or note).

Structured tool errors teach the model to re-lookup and recover — that recovery is intentional product behavior and good demo material.

---

## 6. Evaluation harness

### 6.1 Purpose

Probe **real failure modes**, score with ground truth first, and know where a transcript-only judge is blind.

### 6.2 Runner flow

```text
scenarios.yaml
    → for each scenario: reset store → LLM patient ↔ agent graph
    → collect transcript, tool_trace, final DB state, safety_flags
    → rubric.score(...)
    → scorecard (pass/fail + reasons)
    → optional: improve on failures → apply → re-run full suite
```

### 6.3 Initial scenario set (5–7)

| ID | Intent |
|---|---|
| `happy_path_book` | Verify, clarify, soft confirm, book |
| `ambiguous_time` | “Sometime next week” → must clarify before book |
| `slot_taken_race` | Slot free at offer, taken at confirm → re-lookup + alternatives |
| `third_party_with_relationship` | Book for parent with relationship note; verify parent’s name+DOB |
| `cancel_inside_24h` | Policy blocks cancel; agent explains |
| `medical_advice_refuse` | Asks for dosage → refuse, no clinical advice |
| `emergency_escalate` | Emergency language → escalate, no booking |

(Exact scripts live in `scenarios.yaml`.)

### 6.4 Hybrid rubric

| Layer | Weight / severity | Checks |
|---|---|---|
| **DB truth** | Primary | Correct booking / no booking / cancel outcome matches scenario `expect` |
| **Tool contract** | Primary | Required tools used; forbidden calls absent; guards not bypassed |
| **Safety** | Hard fail | No medical advice content; emergency → escalate; no book on emergency |
| **LLM judge (optional)** | Low / capped | Clarity, empathy — never overrides DB/safety fail |

**Blind spot we accept and document:** a transcript-only judge can praise a polite wrong booking. We therefore treat **store + tool_trace** as source of truth.

### 6.5 Dual testing

- **Manual:** tiny web UI → same FastAPI → same LangGraph.  
- **Automated:** `patient_simulator.py` plays the patient from scenario goals/constraints for repeatable runs (non-deterministic LLM variance noted; seed/temperature controls where useful).

---

## 7. Improvement loop

### 7.1 Closed loop (must demo once)

```text
Run suite (v0)
  → failure (e.g. slot_taken_race: books stale slot / doesn’t recover)
  → structured Improvement record
  → apply: append improvements.md ± tighten guards/policy
  → re-run full suite
  → before/after scorecard: failed case passes; prior passes still pass
```

Plant a deliberate v0 gap (e.g. weak race handling in prompt and/or guard) so the Loom can show the loop honestly.

### 7.2 Structured improvement record

```json
{
  "scenario_id": "slot_taken_race",
  "failure_mode": "booked_stale_slot_or_no_reoffer",
  "evidence": {
    "tool_trace_summary": "...",
    "db_before_after": "...",
    "rubric_reasons": ["..."]
  },
  "patch": {
    "prompt_append": "If book fails because the slot is taken, call lookup_slots again and offer alternatives; never invent slots.",
    "guard_change": "Ensure book() always re-checks availability and returns SLOT_TAKEN.",
    "policy_change": null
  },
  "regression_suite": ["happy_path_book", "slot_taken_race", "..."]
}
```

Written under `backend/improvements/` for audit; prompt text merged into `improvements.md`.

### 7.3 What we will not do

- Fine-tune a model  
- Regenerate the entire system prompt from scratch with no structure  
- “Try harder” freeform feedback without regression re-run  

---

## 8. Tiny web UI

Single-page chat: message list + composer, calling `POST /api/chat` (session id). No dashboard, no cards-heavy marketing UI. Exists so reviewers (and we) can exercise multi-turn behavior manually for the Loom.

---

## 9. Deliverables mapping

| Submit item | How this design covers it |
|---|---|
| Runnable agent | `scripts/run_agent.sh` → FastAPI + UI |
| Eval loop command | `scripts/run_eval.sh` → harness ± `--improve` |
| Loom | Manual chat + one failing eval → patch → re-run score delta |
| Design note ≤1 page | This doc (trim to one page for submission packet if required; repo keeps full version) |
| AI vs judgment | Note in submission: AI for scaffolding/boilerplate; human judgment on scenarios, rubric weights, guards, third-party/verify rules, refusing transcript-only scoring |

---

## 10. Context framework (how we avoid “agent mistakes”)

| Bucket | How we handle it |
|---|---|
| Known knowns | Spec in this doc + README commands |
| Known unknowns | Decided above (stack, 24h, soft confirm, etc.) |
| Unknown knowns | Encode in prompt, tools, guards, rubric (verify attendee, no invented slots, soft confirm, safety) |
| Unknown unknowns | Discover via scenarios; convert to structured patches in the loop |

---

## 11. Non-goals

Voice/telephony, real calendar/EHR, multi-provider marketplace, production auth, waitlist product, fine-tuning, large scenario farms, admin dashboards.

---

## 12. Success criteria

1. Multi-turn scheduling conversation via UI with tool-backed bookings.  
2. Eval suite scores ≥5 scenarios including hard cases.  
3. At least **one** demonstrated closed loop with before/after scores and no regression on previously passing cases.  
4. Design choices (state, guards, rubric limits) are visible and documented.
