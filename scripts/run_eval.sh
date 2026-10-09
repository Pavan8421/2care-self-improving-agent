#!/usr/bin/env bash
# One-command eval + improvement loop (filled in when harness is runnable).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
# Convention: always run from backend/ so `app.*` / `eval.*` import cleanly.
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "TODO: python -m eval.harness --improve (Phase 5–6)"
echo "Smoke check: python scripts/smoke_phase0.py"
