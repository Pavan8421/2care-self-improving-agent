"""Fake clinic domain: in-memory store + policy helpers."""

from app.clinic.policy import PolicyDecision, can_cancel_or_reschedule
from app.clinic.store import ClinicStore, default_store

__all__ = [
    "ClinicStore",
    "PolicyDecision",
    "can_cancel_or_reschedule",
    "default_store",
]
