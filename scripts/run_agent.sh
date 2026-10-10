#!/usr/bin/env bash
# One-command agent + tiny web UI (Phase 4 / 7).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"

if [[ ! -d .venv ]]; then
  echo "Missing backend/.venv — create it first:"
  echo "  cd backend && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi
# shellcheck disable=SC1091
source .venv/bin/activate

if [[ ! -f .env ]]; then
  echo "Warning: backend/.env missing. Copy .env.example → .env and set OPENAI_API_KEY."
elif ! grep -qE '^OPENAI_API_KEY=sk-' .env 2>/dev/null || grep -qE '^OPENAI_API_KEY=sk-your-key' .env 2>/dev/null; then
  echo "Warning: OPENAI_API_KEY looks unset/placeholder in backend/.env — chat will not work until set."
fi

HOST="${APP_HOST:-0.0.0.0}"
PORT="${APP_PORT:-8000}"

echo "Starting clinic scheduling agent"
echo "  UI:  http://127.0.0.1:${PORT}/"
echo "  API: http://127.0.0.1:${PORT}/api/health"
echo "(Ctrl+C to stop)"
exec uvicorn app.main:app --host "$HOST" --port "$PORT"
