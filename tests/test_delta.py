"""Guarded delta, eligibility, and kW→kWh conversion tests."""

from __future__ import annotations

import pytest

from ges_intel.domain.constants import INTERVAL_HOURS, RELATIVE_THRESHOLD
from ges_intel.domain.delta import (
    cash_loss_usd,
    evaluate_performance,
    lost_energy_kwh,
    power_to_energy_kwh,
    relative_deviation,
)


class TestDelta:
    def test_night_does_not_divide_and_is_not_a_breach(self) -> None:
        perf = evaluate_performance(0.0, 0.0, 0.0, 100.0)
        assert perf.eligible is False
        assert perf.is_breach is False
        assert perf.relative_deviation is None

    def test_tiny_expected_is_ineligible_even_if_ratio_would_be_huge(self) -> None:
        perf = evaluate_performance(0.0, 0.2, 40.0, 100.0)
        assert perf.eligible is False
        assert perf.is_breach is False

    def test_ten_percent_is_a_breach(self) -> None:
        perf = evaluate_performance(90.0, 100.0, 800.0, 150.0)
        assert perf.eligible is True
        assert perf.relative_deviation == pytest.approx(0.10)
        assert perf.is_breach is True

    def test_just_under_threshold_is_healthy(self) -> None:
        perf = evaluate_performance(90.1, 100.0, 800.0, 150.0)
        assert perf.is_breach is False
        assert perf.relative_deviation < RELATIVE_THRESHOLD

    def test_relative_deviation_raises_on_zero_expected(self) -> None:
        with pytest.raises(ZeroDivisionError):
            relative_deviation(0.0, 0.0)

    def test_kwh_is_kw_times_quarter_hour_not_raw_kw(self) -> None:
        lost = lost_energy_kwh(100.0, 60.0)
        assert lost == pytest.approx(10.0)
        assert lost != pytest.approx(40.0)
        assert power_to_energy_kwh(100.0) == pytest.approx(25.0)
        assert INTERVAL_HOURS == pytest.approx(0.25)
        assert cash_loss_usd(10.0, 0.10) == pytest.approx(1.0)

    def test_overperformance_does_not_create_negative_loss(self) -> None:
        assert lost_energy_kwh(80.0, 100.0) == 0.0

    def test_clips_actual_to_capacity_before_delta(self) -> None:
        perf = evaluate_performance(500.0, 80.0, 900.0, 100.0)
        assert perf.actual_power_kw == 100.0
