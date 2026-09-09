"""Synthetic sensor generator tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from ges_intel.domain.constants import (
    DEFAULT_SEED,
    INTERVAL_MINUTES,
    IRRADIANCE_FLOOR_WM2,
    SOILING_MAX,
    SOILING_MIN,
)
from ges_intel.domain.models import Inverter
from ges_intel.infrastructure.generator import DEFAULT_FLEET, generate_readings, local_hour


class TestGenerator:
    def test_seed_is_reproducible(self) -> None:
        a = generate_readings(seed=DEFAULT_SEED, n_days=2)
        b = generate_readings(seed=DEFAULT_SEED, n_days=2)
        assert [(r.ts_utc, r.actual_power_kw, r.is_anomaly) for r in a] == [
            (r.ts_utc, r.actual_power_kw, r.is_anomaly) for r in b
        ]

    def test_fleet_and_cadence(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=1)
        ids = {r.inverter_id for r in rows}
        assert ids == {inv.id for inv in DEFAULT_FLEET}
        assert len(rows) == 5 * 96

    def test_timestamps_are_utc_quarter_hours(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=1, fleet=DEFAULT_FLEET[:1])
        assert all(r.ts_utc.tzinfo is not None for r in rows)
        minutes = {r.ts_utc.minute for r in rows}
        assert minutes <= {0, 15, 30, 45}

    def test_night_is_never_labeled_anomaly(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=3)
        night = [r for r in rows if r.irradiance_wm2 < IRRADIANCE_FLOOR_WM2]
        assert night
        assert all(not r.is_anomaly for r in night)
        assert all(r.actual_power_kw == 0.0 for r in night)

    def test_soiling_is_between_12_and_15_percent(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=10)
        soiled = [r for r in rows if r.anomaly_type == "soiling" and r.true_expected_kw > 5]
        assert soiled
        ratios = [(r.true_expected_kw - r.actual_power_kw) / r.true_expected_kw for r in soiled]
        mean_ratio = sum(ratios) / len(ratios)
        assert SOILING_MIN - 0.04 <= mean_ratio <= SOILING_MAX + 0.05
        assert min(ratios) > 0.08

    def test_shading_events_exist(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=10)
        shaded = [r for r in rows if r.anomaly_type == "shading"]
        assert shaded

    def test_hidden_labels_present(self) -> None:
        rows = generate_readings(seed=DEFAULT_SEED, n_days=5)
        assert any(r.is_anomaly for r in rows)
        assert all(r.true_expected_kw >= 0 for r in rows)

    def test_actual_never_exceeds_capacity(self) -> None:
        cap = {inv.id: inv.capacity_kw for inv in DEFAULT_FLEET}
        for r in generate_readings(seed=DEFAULT_SEED, n_days=2):
            assert 0 <= r.actual_power_kw <= cap[r.inverter_id]
            assert 0 <= r.true_expected_kw <= cap[r.inverter_id]

    def test_local_hour_uses_istanbul(self) -> None:
        ts = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        assert local_hour(ts) == pytest.approx(12.0)

    def test_custom_tiny_fleet(self) -> None:
        inv = Inverter("T-1", "test", 50.0, 37.87, 32.49, "Europe/Istanbul")
        rows = generate_readings(seed=1, n_days=1, fleet=[inv])
        assert {r.inverter_id for r in rows} == {"T-1"}
        assert len(rows) == 96

    def test_slot_spacing_is_15_minutes(self) -> None:
        rows = [r for r in generate_readings(seed=1, n_days=1, fleet=DEFAULT_FLEET[:1])]
        rows.sort(key=lambda r: r.ts_utc)
        assert rows[1].ts_utc - rows[0].ts_utc == timedelta(minutes=INTERVAL_MINUTES)
