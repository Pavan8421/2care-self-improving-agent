#!/usr/bin/env bash
# One-command agent + tiny web UI (filled in when app is runnable).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
# Convention: always run from backend/ so `app.*` / `eval.*` import cleanly.
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi
echo "TODO: start uvicorn app.main:app and serve frontend (Phase 4)"
echo "Smoke check: python scripts/smoke_phase0.py"
