"""Consecutive-slot streak tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from ges_intel.domain.alerting import (
    align_slot,
    evaluate_streak,
    should_open_alert,
    should_resolve_alert,
    slot_window_ending_at,
)
from ges_intel.domain.models import TelemetryRecord


def _ts(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 6, 15, hour, minute, tzinfo=timezone.utc)


def _record(
    ts: datetime,
    *,
    eligible: bool,
    breach: bool,
    expected: float = 50.0,
    actual: float = 40.0,
) -> TelemetryRecord:
    return TelemetryRecord(
        inverter_id="INV-01",
        ts_utc=align_slot(ts),
        irradiance_wm2=800.0 if eligible else 0.0,
        temperature_c=30.0,
        actual_power_kw=actual,
        expected_power_kw=expected,
        delta_kw=expected - actual,
        relative_deviation=(expected - actual) / expected if eligible else None,
        eligible=eligible,
        is_breach=breach,
    )


class TestAlerting:
    def test_aligns_to_utc_quarter_hours(self) -> None:
        ts = datetime(2026, 6, 15, 12, 7, 33, tzinfo=timezone.utc)
        assert align_slot(ts) == datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

    def test_naive_timestamp_treated_as_utc(self) -> None:
        naive = datetime(2026, 6, 15, 12, 0)
        assert align_slot(naive).tzinfo is timezone.utc

    def test_window_is_four_calendar_slots(self) -> None:
        end = _ts(12, 45)
        slots = slot_window_ending_at(end, 4)
        assert slots == [_ts(12, 0), _ts(12, 15), _ts(12, 30), _ts(12, 45)]

    def test_four_breaches_open_alert(self) -> None:
        end = _ts(12, 45)
        rows = {
            align_slot(end - timedelta(minutes=15 * i)): _record(
                end - timedelta(minutes=15 * i), eligible=True, breach=True
            )
            for i in range(4)
        }
        streak = evaluate_streak(rows, end)
        assert should_open_alert(streak)
        assert not should_resolve_alert(streak)

    def test_three_breaches_do_not_open(self) -> None:
        end = _ts(12, 45)
        rows = {}
        for i, breach in enumerate((True, True, True, False)):
            ts = end - timedelta(minutes=15 * (3 - i))
            rows[align_slot(ts)] = _record(ts, eligible=True, breach=breach)
        streak = evaluate_streak(rows, end)
        assert not should_open_alert(streak)

    def test_gap_resets_streak(self) -> None:
        end = _ts(12, 45)
        rows = {}
        for ts in (_ts(12, 0), _ts(12, 15), _ts(12, 45)):
            rows[ts] = _record(ts, eligible=True, breach=True)
        streak = evaluate_streak(rows, end)
        assert streak.complete is False
        assert not should_open_alert(streak)

    def test_ineligible_night_slot_breaks_streak(self) -> None:
        end = _ts(4, 45)
        rows = {}
        for i in range(4):
            ts = end - timedelta(minutes=15 * (3 - i))
            rows[ts] = _record(ts, eligible=False, breach=False, expected=0.0, actual=0.0)
        streak = evaluate_streak(rows, end)
        assert not should_open_alert(streak)
        assert not should_resolve_alert(streak)

    def test_four_healthy_eligible_resolves(self) -> None:
        end = _ts(13, 0)
        rows = {
            align_slot(end - timedelta(minutes=15 * i)): _record(
                end - timedelta(minutes=15 * i),
                eligible=True,
                breach=False,
                actual=98.0,
                expected=100.0,
            )
            for i in range(4)
        }
        streak = evaluate_streak(rows, end)
        assert should_resolve_alert(streak)
