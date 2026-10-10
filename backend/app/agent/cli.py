"""Interactive multi-turn CLI for Phase 3 manual chat.

Usage (from backend/):
  python -m app.agent.cli
  python -m app.agent.cli --session demo
"""

from __future__ import annotations

import argparse
import json
import sys

from app.agent.graph import SchedulingSession
from app.clinic.store import ClinicStore
from app.config import get_settings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clinic scheduling agent CLI")
    parser.add_argument("--session", default=None, help="Optional fixed session id")
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Print state snapshot after each turn",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    if not settings.has_openai_api_key():
        print(
            "OPENAI_API_KEY missing or still a placeholder in backend/.env — "
            "set a real key before chatting.",
            file=sys.stderr,
        )
        return 1

    session = SchedulingSession(store=ClinicStore(), session_id=args.session)
    print(f"session_id={session.session_id}")
    print("Scheduling agent ready. Type a message (or /quit, /reset).\n")

    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line in ("/quit", "/exit", "quit", "exit"):
            break
        if line == "/reset":
            store = session.ctx.store
            session = SchedulingSession(store=store)
            print(f"(reset) session_id={session.session_id}")
            continue

        turn = session.invoke(line)
        print(f"Agent: {turn.reply}\n")
        if args.debug:
            print(json.dumps(turn.state, indent=2, default=str))
            print()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
