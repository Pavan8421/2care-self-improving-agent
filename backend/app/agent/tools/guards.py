"""Code-level tool guards (verify, soft-confirm, slot re-check, policy)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.clinic.store import ClinicStore, StoreError, default_store


@dataclass
class AgentToolContext:
    """Mutable session facts the orchestrator / tools share.

    Phase 3 LangGraph state will mirror these fields; tools close over one
    context instance per conversation.
    """

    store: ClinicStore = field(default_factory=lambda: default_store)
    verified_patient_id: str | None = None
    relationship: str | None = None
    pending_slot_id: str | None = None
    soft_confirmed: bool = False
    soft_confirmed_slot_id: str | None = None
    booking_id: str | None = None
    escalated: bool = False
    escalate_reason: str | None = None
    safety_flags: list[str] = field(default_factory=list)

    def reset_session_flags(self) -> None:
        """Clear conversation gates; keep the same store instance."""
        self.verified_patient_id = None
        self.relationship = None
        self.pending_slot_id = None
        self.soft_confirmed = False
        self.soft_confirmed_slot_id = None
        self.booking_id = None
        self.escalated = False
        self.escalate_reason = None
        self.safety_flags = []

    def mark_soft_confirmed(self, slot_id: str) -> None:
        self.pending_slot_id = slot_id
        self.soft_confirmed = True
        self.soft_confirmed_slot_id = slot_id


def _err(code: str, message: str) -> dict[str, Any]:
    return StoreError(code=code, message=message).to_dict()


def require_not_escalated(ctx: AgentToolContext) -> dict[str, Any] | None:
    if ctx.escalated:
        return _err(
            "SESSION_ESCALATED",
            "This session was escalated; scheduling actions are blocked.",
        )
    return None


def require_verified_patient(
    ctx: AgentToolContext,
    patient_id: str | None = None,
) -> dict[str, Any] | None:
    if not ctx.verified_patient_id:
        return _err(
            "NOT_VERIFIED",
            "Verify the appointment patient with get_patient(name, dob) first.",
        )
    if patient_id and patient_id != ctx.verified_patient_id:
        return _err(
            "PATIENT_MISMATCH",
            "patient_id does not match the verified appointment patient.",
        )
    return None


def require_relationship(ctx: AgentToolContext, relationship: str | None) -> dict[str, Any] | None:
    rel = (relationship if relationship is not None else ctx.relationship) or ""
    if not rel.strip():
        return _err(
            "RELATIONSHIP_REQUIRED",
            "Provide relationship: 'self' or a short note (e.g. parent, spouse).",
        )
    return None


def require_soft_confirm(ctx: AgentToolContext, slot_id: str) -> dict[str, Any] | None:
    if not ctx.soft_confirmed or not ctx.soft_confirmed_slot_id:
        return _err(
            "SOFT_CONFIRM_REQUIRED",
            "Patient must soft-confirm the slot before booking.",
        )
    if ctx.soft_confirmed_slot_id != slot_id:
        return _err(
            "SOFT_CONFIRM_SLOT_MISMATCH",
            (
                f"Soft confirm was for {ctx.soft_confirmed_slot_id}, "
                f"not {slot_id}. Confirm the offered slot again."
            ),
        )
    return None


def guard_book(
    ctx: AgentToolContext,
    *,
    slot_id: str,
    patient_id: str,
    relationship: str,
) -> dict[str, Any] | None:
    """Return an error dict if book must be rejected; else None."""
    if err := require_not_escalated(ctx):
        return err
    if err := require_verified_patient(ctx, patient_id):
        return err
    if err := require_relationship(ctx, relationship):
        return err
    if err := require_soft_confirm(ctx, slot_id):
        return err
    return None


def guard_cancel_or_reschedule(
    ctx: AgentToolContext,
    *,
    booking_id: str,
) -> dict[str, Any] | None:
    if err := require_not_escalated(ctx):
        return err
    if err := require_verified_patient(ctx):
        return err

    got = ctx.store.get_booking(booking_id=booking_id, include_cancelled=True)
    if not got.get("ok"):
        return got
    booking = got["booking"]
    if booking["patient_id"] != ctx.verified_patient_id:
        return _err(
            "BOOKING_PATIENT_MISMATCH",
            "Booking does not belong to the verified appointment patient.",
        )
    return None


def apply_successful_patient_lookup(ctx: AgentToolContext, result: dict[str, Any]) -> None:
    if result.get("ok") and result.get("patient"):
        ctx.verified_patient_id = result["patient"]["patient_id"]


def apply_successful_book(ctx: AgentToolContext, result: dict[str, Any], relationship: str) -> None:
    if result.get("ok") and result.get("booking"):
        ctx.booking_id = result["booking"]["booking_id"]
        ctx.relationship = relationship.strip()
        ctx.soft_confirmed = False
        ctx.soft_confirmed_slot_id = None
        ctx.pending_slot_id = None
