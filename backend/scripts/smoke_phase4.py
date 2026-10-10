#!/usr/bin/env python3
"""Phase 4 smoke: FastAPI health, static UI, chat + session reset (live needs key)."""

from __future__ import annotations

import sys
from pathlib import Path


def _ensure_backend_on_path() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def main() -> int:
    _ensure_backend_on_path()
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import FRONTEND_DIR, create_app

    _assert(FRONTEND_DIR.is_dir(), f"frontend missing: {FRONTEND_DIR}")
    _assert((FRONTEND_DIR / "index.html").is_file(), "index.html missing")
    _assert((FRONTEND_DIR / "src" / "main.js").is_file(), "main.js missing")

    app = create_app()
    client = TestClient(app)

    health = client.get("/api/health")
    _assert(health.status_code == 200, health.text)
    body = health.json()
    _assert(body.get("ok") is True, body)
    print("ok: /api/health")

    index = client.get("/")
    _assert(index.status_code == 200, index.text)
    _assert("Clinic Scheduling" in index.text, "UI title missing")
    print("ok: / serves UI")

    css = client.get("/src/styles.css")
    js = client.get("/src/main.js")
    _assert(css.status_code == 200 and "composer" in css.text, css.status_code)
    _assert(js.status_code == 200 and "/api/chat" in js.text, js.status_code)
    print("ok: static assets")

    reset = client.post("/api/session/reset", json={})
    _assert(reset.status_code == 200, reset.text)
    sid = reset.json()["session_id"]
    _assert(bool(sid), reset.text)
    print(f"ok: session reset → {sid[:8]}…")

    # Empty message should 422, not 500
    bad = client.post("/api/chat", json={"session_id": sid, "message": ""})
    _assert(bad.status_code == 422, bad.text)
    print("ok: empty message rejected")

    settings = get_settings()
    if not settings.has_openai_api_key():
        print("skip: live /api/chat (OPENAI_API_KEY not set)")
        print("phase4_smoke=ok")
        return 0

    chat = client.post(
        "/api/chat",
        json={
            "session_id": sid,
            "message": "Hi — I only want to say hello, no booking yet.",
            "debug": True,
        },
    )
    _assert(chat.status_code == 200, chat.text)
    data = chat.json()
    _assert(data.get("session_id") == sid, data)
    _assert(bool(data.get("reply")), data)
    print(f"ok: /api/chat reply={data['reply'][:80]!r}")

    # New session must not continue prior transcript
    reset2 = client.post("/api/session/reset", json={"session_id": sid})
    sid2 = reset2.json()["session_id"]
    _assert(sid2 != sid, "reset should mint a new session id")
    chat2 = client.post(
        "/api/chat",
        json={"session_id": sid2, "message": "What is my name?", "debug": True},
    )
    _assert(chat2.status_code == 200, chat2.text)
    print("ok: new session is isolated")

    print("phase4_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
