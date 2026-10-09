"""LangChain tools wrapping the fake clinic store."""

from __future__ import annotations

import json
from typing import Any, Literal

from langchain_core.tools import StructuredTool

from app.agent.tools.guards import (
    AgentToolContext,
    apply_successful_book,
    apply_successful_patient_lookup,
    guard_book,
    guard_cancel_or_reschedule,
    require_not_escalated,
    require_soft_confirm,
)
from app.clinic.store import ClinicStore


def _dumps(payload: dict[str, Any]) -> str:
    return json.dumps(payload, default=str)


def build_clinic_tools(ctx: AgentToolContext) -> list[StructuredTool]:
    """Create clinic tools closed over a shared session context + store."""

    def get_patient(name: str, dob: str) -> str:
        """Look up the appointment patient by full name and date of birth (YYYY-MM-DD)."""
        if err := require_not_escalated(ctx):
            return _dumps(err)
        result = ctx.store.get_patient(name=name, dob=dob)
        apply_successful_patient_lookup(ctx, result)
        return _dumps(result)

    def lookup_slots(date_from: str = "", date_to: str = "") -> str:
        """List free appointment slots. Optional ISO datetimes date_from / date_to."""
        if err := require_not_escalated(ctx):
            return _dumps(err)
        result = ctx.store.lookup_slots(
            date_from=date_from or None,
            date_to=date_to or None,
            free_only=True,
        )
        return _dumps(result)

    def book(slot_id: str, patient_id: str, relationship: str) -> str:
        """Book a slot after patient verify + soft confirm. relationship is 'self' or a note."""
        if err := guard_book(
            ctx,
            slot_id=slot_id,
            patient_id=patient_id,
            relationship=relationship,
        ):
            return _dumps(err)

        rel = relationship.strip()
        result = ctx.store.book(
            slot_id=slot_id,
            patient_id=patient_id,
            relationship=rel,
        )
        # Store already re-checks SLOT_TAKEN; surface as-is.
        apply_successful_book(ctx, result, rel)
        if result.get("ok"):
            ctx.relationship = rel
        return _dumps(result)

    def get_booking(booking_id: str = "", patient_id: str = "") -> str:
        """Fetch a booking by booking_id, or list bookings for patient_id."""
        if err := require_not_escalated(ctx):
            return _dumps(err)
        result = ctx.store.get_booking(
            booking_id=booking_id or None,
            patient_id=patient_id or None,
        )
        return _dumps(result)

    def check_policy(action: Literal["cancel", "reschedule"], booking_id: str) -> str:
        """Check whether cancel or reschedule is allowed under the 24h policy."""
        if err := require_not_escalated(ctx):
            return _dumps(err)
        if action not in ("cancel", "reschedule"):
            return _dumps(
                {
                    "ok": False,
                    "error": {
                        "code": "INVALID_ACTION",
                        "message": "action must be 'cancel' or 'reschedule'.",
                    },
                }
            )
        result = ctx.store.check_policy(action=action, booking_id=booking_id)
        return _dumps(result)

    def cancel(booking_id: str) -> str:
        """Cancel a booking if it belongs to the verified patient and policy allows."""
        if err := guard_cancel_or_reschedule(ctx, booking_id=booking_id):
            return _dumps(err)
        result = ctx.store.cancel(booking_id)
        if result.get("ok"):
            ctx.booking_id = None
        return _dumps(result)

    def reschedule(booking_id: str, new_slot_id: str, relationship: str = "") -> str:
        """Reschedule to new_slot_id after soft confirm of the new slot; 24h policy applies."""
        if err := guard_cancel_or_reschedule(ctx, booking_id=booking_id):
            return _dumps(err)
        if err := require_soft_confirm(ctx, new_slot_id):
            return _dumps(err)

        result = ctx.store.reschedule(
            booking_id=booking_id,
            new_slot_id=new_slot_id,
            relationship=relationship or ctx.relationship,
        )
        if result.get("ok"):
            ctx.booking_id = result["booking"]["booking_id"]
            ctx.soft_confirmed = False
            ctx.soft_confirmed_slot_id = None
            ctx.pending_slot_id = None
        return _dumps(result)

    def escalate(reason: str) -> str:
        """Escalate the session (emergency, abuse, or admin handoff). Blocks further booking."""
        reason_clean = (reason or "").strip() or "unspecified"
        ctx.escalated = True
        ctx.escalate_reason = reason_clean
        if reason_clean.lower() not in ctx.safety_flags:
            ctx.safety_flags.append(reason_clean.lower())
        if "emergency" in reason_clean.lower() and "emergency" not in ctx.safety_flags:
            ctx.safety_flags.append("emergency")
        return _dumps(
            {
                "ok": True,
                "escalated": True,
                "reason": reason_clean,
                "message": (
                    "Session escalated. Advise contacting emergency services if this "
                    "is a medical emergency. Do not continue booking in this turn."
                ),
            }
        )

    tools = [
        StructuredTool.from_function(
            func=get_patient,
            name="get_patient",
            description=(
                "Verify the appointment patient (who will attend) by full name and "
                "DOB (YYYY-MM-DD). Call this before booking."
            ),
        ),
        StructuredTool.from_function(
            func=lookup_slots,
            name="lookup_slots",
            description=(
                "Return free clinic slots. Optional date_from / date_to as ISO datetimes. "
                "Only offer times from this tool."
            ),
        ),
        StructuredTool.from_function(
            func=book,
            name="book",
            description=(
                "Book slot_id for verified patient_id after soft confirmation. "
                "relationship must be 'self' or a short note (parent, spouse, …)."
            ),
        ),
        StructuredTool.from_function(
            func=get_booking,
            name="get_booking",
            description="Get one booking by booking_id, or list bookings for patient_id.",
        ),
        StructuredTool.from_function(
            func=check_policy,
            name="check_policy",
            description="Check 24h policy for action 'cancel' or 'reschedule' on booking_id.",
        ),
        StructuredTool.from_function(
            func=cancel,
            name="cancel",
            description="Cancel booking_id if verified patient owns it and policy allows.",
        ),
        StructuredTool.from_function(
            func=reschedule,
            name="reschedule",
            description=(
                "Move booking_id to new_slot_id after soft confirm of the new slot; "
                "enforces 24h policy and slot availability."
            ),
        ),
        StructuredTool.from_function(
            func=escalate,
            name="escalate",
            description=(
                "Escalate for emergency / abuse / admin handoff. "
                "Stops further scheduling in this session."
            ),
        ),
    ]
    return tools


def build_default_tools(store: ClinicStore | None = None) -> tuple[AgentToolContext, list[StructuredTool]]:
    """Convenience for scripts: fresh context, optional dedicated store."""
    ctx = AgentToolContext(store=store or ClinicStore())
    return ctx, build_clinic_tools(ctx)
