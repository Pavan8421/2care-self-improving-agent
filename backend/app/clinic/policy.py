"""Clinic policy rules (e.g. 24h cancel window)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

CANCEL_NOTICE = timedelta(hours=24)


@dataclass(frozen=True)
class PolicyDecision:
    allowed: bool
    reason: str
    code: str


def ensure_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def can_cancel_or_reschedule(
    appointment_start: datetime,
    *,
    as_of: datetime | None = None,
    min_notice: timedelta = CANCEL_NOTICE,
) -> PolicyDecision:
    """Allow cancel/reschedule only if appointment is at least `min_notice` away."""
    start = ensure_utc(appointment_start)
    now = ensure_utc(as_of or datetime.now(timezone.utc))
    delta = start - now

    if delta < timedelta(0):
        return PolicyDecision(
            allowed=False,
            reason="Appointment start time is already in the past.",
            code="APPOINTMENT_PAST",
        )

    if delta < min_notice:
        hours = int(min_notice.total_seconds() // 3600)
        return PolicyDecision(
            allowed=False,
            reason=(
                f"Changes require at least {hours} hours notice. "
                f"This appointment starts in {int(delta.total_seconds() // 3600)} hours."
            ),
            code="WITHIN_NOTICE_WINDOW",
        )

    return PolicyDecision(
        allowed=True,
        reason=f"Allowed: more than {int(min_notice.total_seconds() // 3600)} hours before start.",
        code="OK",
    )
