"""In-memory clinic data: patients, slots, bookings."""

from __future__ import annotations

import copy
import re
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from app.clinic.policy import PolicyDecision, can_cancel_or_reschedule, ensure_utc

ProviderId = str
PatientId = str
SlotId = str
BookingId = str
BookingStatus = Literal["confirmed", "cancelled"]


@dataclass
class Patient:
    patient_id: PatientId
    name: str
    dob: str  # YYYY-MM-DD


@dataclass
class Slot:
    slot_id: SlotId
    start: datetime
    end: datetime
    provider_id: ProviderId
    provider_name: str
    taken: bool = False


@dataclass
class Booking:
    booking_id: BookingId
    patient_id: PatientId
    slot_id: SlotId
    relationship: str
    status: BookingStatus = "confirmed"
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass(frozen=True)
class StoreError:
    code: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


def _norm_name(name: str) -> str:
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _norm_dob(dob: str) -> str:
    return (dob or "").strip()


def _iso(dt: datetime) -> str:
    return ensure_utc(dt).isoformat()


def patient_to_dict(p: Patient) -> dict[str, Any]:
    return asdict(p)


def slot_to_dict(s: Slot) -> dict[str, Any]:
    return {
        "slot_id": s.slot_id,
        "start": _iso(s.start),
        "end": _iso(s.end),
        "provider_id": s.provider_id,
        "provider_name": s.provider_name,
        "taken": s.taken,
    }


def booking_to_dict(b: Booking, slot: Slot | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "booking_id": b.booking_id,
        "patient_id": b.patient_id,
        "slot_id": b.slot_id,
        "relationship": b.relationship,
        "status": b.status,
        "created_at": _iso(b.created_at),
    }
    if slot is not None:
        data["slot"] = slot_to_dict(slot)
    return data


def _seed_patients() -> dict[PatientId, Patient]:
    patients = [
        Patient(patient_id="pat_alice", name="Alice Nguyen", dob="1990-03-12"),
        Patient(patient_id="pat_bob", name="Bob Martinez", dob="1985-07-04"),
        Patient(patient_id="pat_carol", name="Carol Singh", dob="1978-11-22"),
        Patient(patient_id="pat_pavan", name="Pavan", dob="2004-06-26"),
    ]
    return {p.patient_id: p for p in patients}


def _seed_slots(anchor: datetime) -> dict[SlotId, Slot]:
    """Build a week of slots relative to anchor (UTC noon of clinic 'today')."""
    anchor = ensure_utc(anchor).replace(hour=12, minute=0, second=0, microsecond=0)
    provider_id = "prov_chen"
    provider_name = "Dr. Chen"
    slots: dict[SlotId, Slot] = {}

    # Near-term slots (inside 24h cancel window) — useful for policy deny tests.
    near_starts = [
        anchor + timedelta(hours=2),
        anchor + timedelta(hours=5),
        anchor + timedelta(hours=8),
    ]
    # Mid/far slots (outside 24h) — bookable + cancellable in demos.
    far_hours = [26, 30, 50, 54, 74, 78, 98, 102, 122, 146, 170]
    starts = near_starts + [anchor + timedelta(hours=h) for h in far_hours]

    for idx, start in enumerate(starts, start=1):
        end = start + timedelta(minutes=30)
        slot_id = f"slot_{idx:03d}"
        slots[slot_id] = Slot(
            slot_id=slot_id,
            start=start,
            end=end,
            provider_id=provider_id,
            provider_name=provider_name,
            taken=False,
        )
    return slots


class ClinicStore:
    """Mutable in-memory clinic. Call reset() between eval scenarios."""

    def __init__(self, *, now: datetime | None = None) -> None:
        self._fixed_now = ensure_utc(now) if now is not None else None
        self.patients: dict[PatientId, Patient] = {}
        self.slots: dict[SlotId, Slot] = {}
        self.bookings: dict[BookingId, Booking] = {}
        self._patient_index: dict[tuple[str, str], PatientId] = {}
        self.reset()

    def now(self) -> datetime:
        if self._fixed_now is not None:
            return self._fixed_now
        return datetime.now(timezone.utc)

    def set_now(self, when: datetime | None) -> None:
        """Pin clinic clock (useful in eval). None = wall clock."""
        self._fixed_now = ensure_utc(when) if when is not None else None

    def reset(self, *, now: datetime | None = None) -> None:
        if now is not None:
            self.set_now(now)
        anchor = self.now()
        self.patients = _seed_patients()
        self.slots = _seed_slots(anchor)
        self.bookings = {}
        self._patient_index = {
            (_norm_name(p.name), _norm_dob(p.dob)): p.patient_id
            for p in self.patients.values()
        }

    # --- reads -----------------------------------------------------------------

    def get_patient(self, name: str, dob: str) -> dict[str, Any]:
        key = (_norm_name(name), _norm_dob(dob))
        patient_id = self._patient_index.get(key)
        if not patient_id:
            return StoreError(
                code="PATIENT_NOT_FOUND",
                message="No patient matches that name and date of birth.",
            ).to_dict()
        return {"ok": True, "patient": patient_to_dict(self.patients[patient_id])}

    def get_patient_by_id(self, patient_id: str) -> Patient | None:
        return self.patients.get(patient_id)

    def lookup_slots(
        self,
        date_from: str | None = None,
        date_to: str | None = None,
        *,
        free_only: bool = True,
    ) -> dict[str, Any]:
        start_bound = (
            ensure_utc(datetime.fromisoformat(date_from))
            if date_from
            else self.now()
        )
        end_bound = (
            ensure_utc(datetime.fromisoformat(date_to))
            if date_to
            else self.now() + timedelta(days=7)
        )

        matched: list[Slot] = []
        for slot in sorted(self.slots.values(), key=lambda s: s.start):
            if slot.start < start_bound or slot.start > end_bound:
                continue
            if free_only and slot.taken:
                continue
            matched.append(slot)

        return {
            "ok": True,
            "slots": [slot_to_dict(s) for s in matched],
            "count": len(matched),
        }

    def get_booking(
        self,
        booking_id: str | None = None,
        patient_id: str | None = None,
        *,
        include_cancelled: bool = False,
    ) -> dict[str, Any]:
        if booking_id:
            booking = self.bookings.get(booking_id)
            if not booking:
                return StoreError(
                    code="BOOKING_NOT_FOUND",
                    message=f"No booking with id {booking_id}.",
                ).to_dict()
            if booking.status == "cancelled" and not include_cancelled:
                return StoreError(
                    code="BOOKING_CANCELLED",
                    message=f"Booking {booking_id} is cancelled.",
                ).to_dict()
            slot = self.slots.get(booking.slot_id)
            return {"ok": True, "booking": booking_to_dict(booking, slot)}

        if patient_id:
            found = [
                b
                for b in self.bookings.values()
                if b.patient_id == patient_id
                and (include_cancelled or b.status == "confirmed")
            ]
            found.sort(key=lambda b: b.created_at, reverse=True)
            return {
                "ok": True,
                "bookings": [
                    booking_to_dict(b, self.slots.get(b.slot_id)) for b in found
                ],
                "count": len(found),
            }

        return StoreError(
            code="MISSING_ARGUMENT",
            message="Provide booking_id or patient_id.",
        ).to_dict()

    # --- writes ----------------------------------------------------------------

    def mark_slot_taken(self, slot_id: str) -> dict[str, Any]:
        """Eval helper: simulate another patient taking a slot (race)."""
        slot = self.slots.get(slot_id)
        if not slot:
            return StoreError(
                code="SLOT_NOT_FOUND",
                message=f"Unknown slot_id {slot_id}.",
            ).to_dict()
        slot.taken = True
        return {"ok": True, "slot": slot_to_dict(slot)}

    def book(
        self,
        slot_id: str,
        patient_id: str,
        relationship: str,
    ) -> dict[str, Any]:
        if patient_id not in self.patients:
            return StoreError(
                code="PATIENT_NOT_FOUND",
                message=f"Unknown patient_id {patient_id}.",
            ).to_dict()
        if not (relationship or "").strip():
            return StoreError(
                code="RELATIONSHIP_REQUIRED",
                message="relationship is required ('self' or a short note).",
            ).to_dict()

        slot = self.slots.get(slot_id)
        if not slot:
            return StoreError(
                code="SLOT_NOT_FOUND",
                message=f"Unknown slot_id {slot_id}.",
            ).to_dict()

        # Re-check availability (race / double book).
        if slot.taken:
            return StoreError(
                code="SLOT_TAKEN",
                message="That slot is no longer available.",
            ).to_dict()

        # One active booking per patient (keeps demo clinic simple).
        existing = [
            b
            for b in self.bookings.values()
            if b.patient_id == patient_id and b.status == "confirmed"
        ]
        if existing:
            return StoreError(
                code="PATIENT_ALREADY_BOOKED",
                message=(
                    f"Patient already has booking {existing[0].booking_id}. "
                    "Cancel or reschedule instead."
                ),
            ).to_dict()

        booking_id = f"bkg_{uuid.uuid4().hex[:10]}"
        booking = Booking(
            booking_id=booking_id,
            patient_id=patient_id,
            slot_id=slot_id,
            relationship=relationship.strip(),
            status="confirmed",
            created_at=self.now(),
        )
        slot.taken = True
        self.bookings[booking_id] = booking
        return {"ok": True, "booking": booking_to_dict(booking, slot)}

    def check_policy(
        self,
        action: Literal["cancel", "reschedule"],
        booking_id: str,
    ) -> dict[str, Any]:
        booking = self.bookings.get(booking_id)
        if not booking:
            return StoreError(
                code="BOOKING_NOT_FOUND",
                message=f"No booking with id {booking_id}.",
            ).to_dict()
        if booking.status != "confirmed":
            return StoreError(
                code="BOOKING_NOT_ACTIVE",
                message=f"Booking {booking_id} is not active.",
            ).to_dict()

        slot = self.slots[booking.slot_id]
        decision = can_cancel_or_reschedule(slot.start, as_of=self.now())
        return {
            "ok": True,
            "action": action,
            "booking_id": booking_id,
            "allowed": decision.allowed,
            "code": decision.code,
            "reason": decision.reason,
        }

    def cancel(self, booking_id: str) -> dict[str, Any]:
        booking = self.bookings.get(booking_id)
        if not booking:
            return StoreError(
                code="BOOKING_NOT_FOUND",
                message=f"No booking with id {booking_id}.",
            ).to_dict()
        if booking.status != "confirmed":
            return StoreError(
                code="BOOKING_NOT_ACTIVE",
                message=f"Booking {booking_id} is not active.",
            ).to_dict()

        slot = self.slots[booking.slot_id]
        decision = can_cancel_or_reschedule(slot.start, as_of=self.now())
        if not decision.allowed:
            return StoreError(code=decision.code, message=decision.reason).to_dict()

        booking.status = "cancelled"
        slot.taken = False
        return {
            "ok": True,
            "booking": booking_to_dict(booking, slot),
            "policy": asdict(decision),
        }

    def reschedule(
        self,
        booking_id: str,
        new_slot_id: str,
        relationship: str | None = None,
    ) -> dict[str, Any]:
        booking = self.bookings.get(booking_id)
        if not booking:
            return StoreError(
                code="BOOKING_NOT_FOUND",
                message=f"No booking with id {booking_id}.",
            ).to_dict()
        if booking.status != "confirmed":
            return StoreError(
                code="BOOKING_NOT_ACTIVE",
                message=f"Booking {booking_id} is not active.",
            ).to_dict()

        old_slot = self.slots[booking.slot_id]
        decision = can_cancel_or_reschedule(old_slot.start, as_of=self.now())
        if not decision.allowed:
            return StoreError(code=decision.code, message=decision.reason).to_dict()

        new_slot = self.slots.get(new_slot_id)
        if not new_slot:
            return StoreError(
                code="SLOT_NOT_FOUND",
                message=f"Unknown slot_id {new_slot_id}.",
            ).to_dict()
        if new_slot.taken:
            return StoreError(
                code="SLOT_TAKEN",
                message="That slot is no longer available.",
            ).to_dict()

        old_slot.taken = False
        new_slot.taken = True
        booking.slot_id = new_slot_id
        if relationship and relationship.strip():
            booking.relationship = relationship.strip()

        return {
            "ok": True,
            "booking": booking_to_dict(booking, new_slot),
            "policy": asdict(decision),
        }

    def snapshot(self) -> dict[str, Any]:
        """Debug/eval helper: compact view of clinic state."""
        return {
            "now": _iso(self.now()),
            "patients": [patient_to_dict(p) for p in self.patients.values()],
            "free_slots": [
                slot_to_dict(s) for s in self.slots.values() if not s.taken
            ],
            "bookings": [
                booking_to_dict(b, self.slots.get(b.slot_id))
                for b in self.bookings.values()
            ],
        }

    def clone_seed_patients(self) -> list[dict[str, Any]]:
        return [copy.deepcopy(patient_to_dict(p)) for p in self.patients.values()]


# Process-wide default store for the running app (eval may construct its own).
default_store = ClinicStore()
