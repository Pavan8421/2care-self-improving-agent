#!/usr/bin/env python3
"""Phase 2 smoke: tools + guards without the LangGraph agent."""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path


def _ensure_backend_on_path() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    if str(backend_root) not in sys.path:
        sys.path.insert(0, str(backend_root))


def _assert(cond: bool, msg: str) -> None:
    if not cond:
        raise AssertionError(msg)


def _invoke(tools: dict, tool_name: str, **kwargs):
    tool = tools[tool_name]
    raw = tool.invoke(kwargs)
    return json.loads(raw) if isinstance(raw, str) else raw


def main() -> int:
    _ensure_backend_on_path()
    from app.agent.tools import build_default_tools
    from app.clinic.store import ClinicStore

    fixed_now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    store = ClinicStore(now=fixed_now)
    ctx, tool_list = build_default_tools(store)
    tools = {t.name: t for t in tool_list}
    _assert(set(tools) >= {
        "get_patient",
        "lookup_slots",
        "confirm_slot",
        "book",
        "get_booking",
        "check_policy",
        "cancel",
        "reschedule",
        "escalate",
    }, f"missing tools: {sorted(tools)}")
    print("ok: tools registered")

    # Book without verify → NOT_VERIFIED
    slots = _invoke(tools, "lookup_slots")
    far = next(s for s in slots["slots"] if s["slot_id"] == "slot_004")
    no_verify = _invoke(
        tools,
        "book",
        slot_id=far["slot_id"],
        patient_id="pat_alice",
        relationship="self",
    )
    _assert(no_verify["ok"] is False, no_verify)
    _assert(no_verify["error"]["code"] == "NOT_VERIFIED", no_verify)
    print("ok: book without verify blocked")

    # Verify, but no soft confirm → SOFT_CONFIRM_REQUIRED
    patient = _invoke(tools, "get_patient", name="Alice Nguyen", dob="1990-03-12")
    _assert(patient["ok"] is True, patient)
    _assert(ctx.verified_patient_id == "pat_alice", ctx.verified_patient_id)
    no_confirm = _invoke(
        tools,
        "book",
        slot_id=far["slot_id"],
        patient_id="pat_alice",
        relationship="self",
    )
    _assert(no_confirm["error"]["code"] == "SOFT_CONFIRM_REQUIRED", no_confirm)
    print("ok: book without soft confirm blocked")

    # Soft confirm + book succeeds
    conf = _invoke(tools, "confirm_slot", slot_id=far["slot_id"])
    _assert(conf["ok"] is True and ctx.soft_confirmed, conf)
    booked = _invoke(
        tools,
        "book",
        slot_id=far["slot_id"],
        patient_id="pat_alice",
        relationship="self",
    )
    _assert(booked["ok"] is True, booked)
    print("ok: verified + soft confirm book")

    # Stale slot / race → SLOT_TAKEN (clear structured error)
    ctx.reset_session_flags()
    bob = _invoke(tools, "get_patient", name="Bob Martinez", dob="1985-07-04")
    _assert(bob["ok"], bob)
    near = next(s for s in _invoke(tools, "lookup_slots")["slots"] if s["slot_id"] == "slot_001")
    store.mark_slot_taken(near["slot_id"])
    ctx.mark_soft_confirmed(near["slot_id"])
    stale = _invoke(
        tools,
        "book",
        slot_id=near["slot_id"],
        patient_id="pat_bob",
        relationship="self",
    )
    _assert(stale["ok"] is False and stale["error"]["code"] == "SLOT_TAKEN", stale)
    print("ok: SLOT_TAKEN on stale slot")

    # Escalate blocks further scheduling
    esc = _invoke(tools, "escalate", reason="emergency chest pain")
    _assert(esc["ok"] and esc["escalated"], esc)
    blocked = _invoke(tools, "lookup_slots")
    _assert(blocked["error"]["code"] == "SESSION_ESCALATED", blocked)
    print("ok: escalate blocks tools")

    # Callable without graph — already using tool.invoke only
    print("ok: tools callable without LangGraph")
    print("phase2_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
