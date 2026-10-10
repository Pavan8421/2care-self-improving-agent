# Design note (1 page)

**Product:** text multi-turn clinic scheduling agent with tool-backed bookings, a hybrid eval harness, and a closed improvement loop.

**Stack:** Python, LangChain / LangGraph, OpenAI, FastAPI, tiny static chat UI. Fake in-memory clinic (no real PHI).

**Agent idea:** the LLM is dialogue only. Truth is the store + tools. Code guards enforce verify, soft-confirm, relationship, 24h cancel, and `SLOT_TAKEN` re-check so the model cannot silently cheat.

**Eval idea:** score DB state and tool traces first; safety hard-fails; transcript judges never override ground truth.

**Improvement idea:** on failure, propose a structured patch; **shadow-test** it in memory (up to 3 tries); **commit to disk only if the full suite is all-green**. Known race failure uses a rule map; retries revise via LLM from prior trial evidence.

**AI vs judgment:** AI for scaffolding and dialogue; humans chose scenarios, rubric weights, guards, third-party/verify rules, planted gap, and “shadow then commit.”

Full detail: [DESIGN.md](./DESIGN.md). Run: `./scripts/run_agent.sh`, `./scripts/run_eval.sh`.
