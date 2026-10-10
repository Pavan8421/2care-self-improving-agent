"""Typed session / graph state for the scheduling agent."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

from langgraph.graph.message import add_messages

Intent = Literal["book", "cancel", "reschedule", "info"]


class AgentState(TypedDict, total=False):
    """LangGraph state mirroring DESIGN §4.4 + AgentToolContext gates."""

    messages: Annotated[list[Any], add_messages]
    verified_patient_id: str | None
    relationship: str | None
    intent: Intent | str | None
    constraints: str | None
    candidate_slots: list[dict[str, Any]]
    pending_slot_id: str | None
    soft_confirmed: bool
    soft_confirmed_slot_id: str | None
    booking_id: str | None
    escalated: bool
    escalate_reason: str | None
    safety_flags: list[str]
    tool_trace: Annotated[list[dict[str, Any]], operator.add]


def empty_agent_state() -> dict[str, Any]:
    """Initial values for a new conversation turn batch."""
    return {
        "verified_patient_id": None,
        "relationship": None,
        "intent": None,
        "constraints": None,
        "candidate_slots": [],
        "pending_slot_id": None,
        "soft_confirmed": False,
        "soft_confirmed_slot_id": None,
        "booking_id": None,
        "escalated": False,
        "escalate_reason": None,
        "safety_flags": [],
        "tool_trace": [],
    }


def format_state_summary(state: AgentState | dict[str, Any]) -> str:
    """Compact session facts injected into the system prompt each agent turn."""
    slots = state.get("candidate_slots") or []
    slot_ids = [s.get("slot_id", "?") for s in slots[:8]]
    flags = state.get("safety_flags") or []
    return (
        "## Current session state\n"
        f"- verified_patient_id: {state.get('verified_patient_id')}\n"
        f"- relationship: {state.get('relationship')}\n"
        f"- intent: {state.get('intent')}\n"
        f"- constraints: {state.get('constraints')}\n"
        f"- candidate_slot_ids: {slot_ids}\n"
        f"- pending_slot_id: {state.get('pending_slot_id')}\n"
        f"- soft_confirmed: {state.get('soft_confirmed')} "
        f"(slot={state.get('soft_confirmed_slot_id')})\n"
        f"- booking_id: {state.get('booking_id')}\n"
        f"- escalated: {state.get('escalated')} "
        f"(reason={state.get('escalate_reason')})\n"
        f"- safety_flags: {flags}\n"
    )
