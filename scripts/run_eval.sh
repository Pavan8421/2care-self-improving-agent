#!/usr/bin/env bash
# Eval suite + optional improvement loop (Phases 5–6 / 7).
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

if [[ ! -f .env ]] || grep -qE '^OPENAI_API_KEY=sk-your-key' .env 2>/dev/null || ! grep -qE '^OPENAI_API_KEY=sk-' .env 2>/dev/null; then
  echo "Error: set a real OPENAI_API_KEY in backend/.env before running eval."
  exit 1
fi

echo "Running eval (python -m eval.harness)…"
echo "  Suite only:     ./scripts/run_eval.sh"
echo "  Diagnose:       ./scripts/run_eval.sh --improve --propose"
echo "  Improve loop:   ./scripts/run_eval.sh --improve --apply"
echo "  Results:        backend/eval/results/latest.json"
echo ""
exec python -m eval.harness "$@"
