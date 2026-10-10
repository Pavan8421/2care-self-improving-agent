#!/usr/bin/env bash
# One-command agent + tiny web UI.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
# Convention: always run from backend/ so `app.*` / `eval.*` import cleanly.
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"

echo "Starting clinic scheduling agent at http://127.0.0.1:${PORT}/"
echo "(Set OPENAI_API_KEY in backend/.env for live chat)"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
