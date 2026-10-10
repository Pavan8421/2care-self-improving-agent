#!/usr/bin/env bash
# Eval suite + optional improvement loop (Phase 5–6).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "Running eval (python -m eval.harness)…"
echo "Tips: --improve --propose | --improve --apply (≤3 shadow trials, commit if all-green)"
exec python -m eval.harness "$@"
