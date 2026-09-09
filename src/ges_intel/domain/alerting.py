"""Consecutive 15-minute streak evaluation from persisted slots (no in-memory counters)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ges_intel.domain.constants import (
    HEALTHY_PERIODS_TO_RESOLVE,
    INTERVAL_MINUTES,
    STREAK_PERIODS_REQUIRED,
)
from ges_intel.domain.models import StreakResult, TelemetryRecord

SLOT = timedelta(minutes=INTERVAL_MINUTES)


def to_utc(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def align_slot(ts: datetime) -> datetime:
    """Floor a timestamp to the calendar-aligned 15-minute UTC slot."""
    ts = to_utc(ts).replace(second=0, microsecond=0)
    minute = (ts.minute // INTERVAL_MINUTES) * INTERVAL_MINUTES
    return ts.replace(minute=minute)


def slot_window_ending_at(end: datetime, n: int) -> list[datetime]:
    end = align_slot(end)
    return [end - SLOT * i for i in range(n - 1, -1, -1)]


def evaluate_streak(
    records_by_ts: dict[datetime, TelemetryRecord],
    end: datetime,
    n: int = STREAK_PERIODS_REQUIRED,
) -> StreakResult:
    """Four *calendar* slots must all exist, all be eligible, and all breach/healthy.

    Missing slots or any ineligible (night/low expected) slot break the streak.
    Keys in ``records_by_ts`` must be UTC-aligned slots.
    """
    slots = slot_window_ending_at(end, n)
    rows: list[TelemetryRecord | None] = []
    for slot in slots:
        key = align_slot(slot)
        rows.append(records_by_ts.get(key))
    if any(row is None for row in rows):
        return StreakResult(
            complete=False,
            all_breach=False,
            all_healthy=False,
            slots=slots,
            records=rows,
        )
    if any(not row.eligible for row in rows if row is not None):
        return StreakResult(
            complete=True,
            all_breach=False,
            all_healthy=False,
            slots=slots,
            records=rows,
        )
    all_breach = all(row is not None and row.is_breach for row in rows)
    all_healthy = all(row is not None and not row.is_breach for row in rows)
    return StreakResult(
        complete=True,
        all_breach=all_breach,
        all_healthy=all_healthy,
        slots=slots,
        records=rows,
    )


def should_open_alert(streak: StreakResult) -> bool:
    return (
        streak.complete
        and streak.all_breach
        and len(streak.slots) >= STREAK_PERIODS_REQUIRED
    )


def should_resolve_alert(streak: StreakResult) -> bool:
    return (
        streak.complete
        and streak.all_healthy
        and len(streak.slots) >= HEALTHY_PERIODS_TO_RESOLVE
    )
