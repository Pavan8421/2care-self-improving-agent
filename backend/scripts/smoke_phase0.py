#!/usr/bin/env python3
"""Phase 0 smoke check: imports + settings + optional OpenAI key presence.

Run from repo root or backend/:
  backend/.venv/bin/python backend/scripts/smoke_phase0.py
  # or: cd backend && .venv/bin/python scripts/smoke_phase0.py
"""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_backend_on_path() -> Path:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))
    return backend_root


def main() -> int:
    backend_root = _ensure_backend_on_path()
    print(f"backend_root={backend_root}")

    import fastapi
    import langchain
    import langchain_openai
    import langgraph
    import openai
    from langgraph.graph import StateGraph

    from app.config import get_settings

    print(f"openai={openai.__version__}")
    print(f"langchain={getattr(langchain, '__version__', 'ok')}")
    print(f"langchain_openai=ok ({langchain_openai.__name__})")
    print(f"langgraph={getattr(langgraph, '__version__', 'ok')} StateGraph={StateGraph.__name__}")
    print(f"fastapi={fastapi.__version__}")

    settings = get_settings()
    print(f"agent_model={settings.agent_model}")
    print(f"patient_model={settings.patient_model}")
    print(f"app={settings.app_host}:{settings.app_port}")

    if not settings.has_openai_api_key():
        print(
            "WARN: OPENAI_API_KEY missing or still placeholder. "
            "Set it in backend/.env before Phase 3+ LLM calls."
        )
        print("smoke_imports=ok key_present=no")
        return 0

    # Light client construct only — no network call required for Phase 0.
    _ = openai.OpenAI(api_key=settings.openai_api_key)
    print("smoke_imports=ok key_present=yes")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
