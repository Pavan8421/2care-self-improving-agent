#!/usr/bin/env python3
"""Phase 1 smoke: clinic store + 24h policy exit criteria."""

from __future__ import annotations

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


def main() -> int:
    _ensure_backend_on_path()
    from app.clinic.store import ClinicStore

    fixed_now = datetime(2026, 10, 7, 12, 0, tzinfo=timezone.utc)
    store = ClinicStore(now=fixed_now)

    # 1) Verify patient by name + DOB
    found = store.get_patient("Alice Nguyen", "1990-03-12")
    _assert(found["ok"] is True, f"expected patient match, got {found}")
    _assert(found["patient"]["patient_id"] == "pat_alice", "wrong patient id")
    missing = store.get_patient("Alice Nguyen", "1990-01-01")
    _assert(missing["ok"] is False, "wrong DOB should miss")
    print("ok: get_patient name+DOB")

    # Free far slot (outside 24h) — slot_004 is +26h from noon anchor
    slots = store.lookup_slots()
    _assert(slots["ok"] and slots["count"] > 0, "expected free slots")
    far = next(s for s in slots["slots"] if s["slot_id"] == "slot_004")
    near = next(s for s in slots["slots"] if s["slot_id"] == "slot_001")

    # 2) Book free slot; second book of same slot fails
    book1 = store.book(far["slot_id"], "pat_alice", "self")
    _assert(book1["ok"] is True, f"book failed: {book1}")
    book2 = store.book(far["slot_id"], "pat_bob", "self")
    _assert(book2["ok"] is False and book2["error"]["code"] == "SLOT_TAKEN", book2)
    print("ok: book + SLOT_TAKEN on double book")

    # Race helper
    race_slot = near["slot_id"]
    store.mark_slot_taken(race_slot)
    race_book = store.book(race_slot, "pat_bob", "self")
    _assert(race_book["error"]["code"] == "SLOT_TAKEN", race_book)
    print("ok: mark_slot_taken")

    # 3) Cancel inside 24h rejected; outside allowed
    # Book Carol on another far slot, then try cancel — should allow (>=24h)
    far2 = next(s for s in store.lookup_slots()["slots"] if s["slot_id"] == "slot_005")
    b_far = store.book(far2["slot_id"], "pat_carol", "self")
    _assert(b_far["ok"], b_far)
    policy_ok = store.check_policy("cancel", b_far["booking"]["booking_id"])
    _assert(policy_ok["allowed"] is True, policy_ok)
    cancel_ok = store.cancel(b_far["booking"]["booking_id"])
    _assert(cancel_ok["ok"] is True, cancel_ok)
    print("ok: cancel outside 24h allowed")

    # Reset and book a near slot for Alice, cancel should fail
    store.reset(now=fixed_now)
    near_again = store.lookup_slots()["slots"]
    near_slot = next(s for s in near_again if s["slot_id"] == "slot_001")
    b_near = store.book(near_slot["slot_id"], "pat_alice", "self")
    _assert(b_near["ok"], b_near)
    policy_deny = store.check_policy("cancel", b_near["booking"]["booking_id"])
    _assert(policy_deny["allowed"] is False, policy_deny)
    cancel_deny = store.cancel(b_near["booking"]["booking_id"])
    _assert(
        cancel_deny["ok"] is False
        and cancel_deny["error"]["code"] == "WITHIN_NOTICE_WINDOW",
        cancel_deny,
    )
    print("ok: cancel inside 24h rejected")

    # 4) reset() clears bookings and restores seed slots
    _assert(
        any(s.taken for s in store.slots.values()),
        "some slots should be taken pre-reset",
    )
    store.reset(now=fixed_now)
    _assert(store.bookings == {}, "bookings should be empty after reset")
    _assert(
        all(not s.taken for s in store.slots.values()),
        "all seed slots free after reset",
    )
    _assert(store.lookup_slots()["count"] > 0, "lookup should return free slots")
    print("ok: reset()")

    # Reschedule happy path (far -> far)
    s = store.lookup_slots()["slots"]
    a = next(x for x in s if x["slot_id"] == "slot_004")
    b = next(x for x in s if x["slot_id"] == "slot_006")
    booked = store.book(a["slot_id"], "pat_bob", "spouse")
    moved = store.reschedule(booked["booking"]["booking_id"], b["slot_id"])
    _assert(moved["ok"] is True, moved)
    _assert(moved["booking"]["slot_id"] == b["slot_id"], moved)
    _assert(store.slots[a["slot_id"]].taken is False, "old slot freed")
    print("ok: reschedule")

    print("phase1_smoke=ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
