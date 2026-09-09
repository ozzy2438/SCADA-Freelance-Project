"""Guarded actual-vs-expected delta, eligibility, and energy conversion."""

from __future__ import annotations

from ges_intel.domain.constants import (
    EXPECTED_ABS_FLOOR_KW,
    EXPECTED_PCT_OF_CAPACITY,
    INTERVAL_HOURS,
    INTERVAL_MINUTES,
    IRRADIANCE_FLOOR_WM2,
    RELATIVE_THRESHOLD,
)
from ges_intel.domain.models import Performance
from ges_intel.domain.physics import clip_power_kw


def expected_floor_kw(capacity_kw: float) -> float:
    return max(EXPECTED_ABS_FLOOR_KW, EXPECTED_PCT_OF_CAPACITY * capacity_kw)


def is_eligible(
    irradiance_wm2: float,
    expected_power_kw: float,
    capacity_kw: float,
) -> bool:
    """Night and near-zero expected slots are not evaluated (breaks streaks)."""
    return (
        irradiance_wm2 >= IRRADIANCE_FLOOR_WM2
        and expected_power_kw >= expected_floor_kw(capacity_kw)
    )


def relative_deviation(expected_power_kw: float, actual_power_kw: float) -> float:
    if expected_power_kw <= 0:
        raise ZeroDivisionError("expected_power_kw must be > 0 to form a ratio")
    return (expected_power_kw - actual_power_kw) / expected_power_kw


def lost_energy_kwh(
    expected_power_kw: float,
    actual_power_kw: float,
    interval_minutes: int = INTERVAL_MINUTES,
) -> float:
    """Convert a 15-minute mean-power deficit into energy (kWh)."""
    hours = interval_minutes / 60.0
    return max(0.0, expected_power_kw - actual_power_kw) * hours


def power_to_energy_kwh(power_kw: float, interval_minutes: int = INTERVAL_MINUTES) -> float:
    return power_kw * (interval_minutes / 60.0)


def cash_loss_usd(lost_kwh: float, tariff_usd_per_kwh: float) -> float:
    return max(0.0, lost_kwh) * tariff_usd_per_kwh


def evaluate_performance(
    actual_power_kw: float,
    expected_power_kw: float,
    irradiance_wm2: float,
    capacity_kw: float,
    *,
    threshold: float = RELATIVE_THRESHOLD,
) -> Performance:
    """Compare actual vs expected with night/low-power gating.

    Ineligible slots never divide by expected and never count as a breach.
    """
    expected = clip_power_kw(expected_power_kw, capacity_kw)
    actual = clip_power_kw(actual_power_kw, capacity_kw)
    delta = expected - actual
    eligible = is_eligible(irradiance_wm2, expected, capacity_kw)
    if not eligible:
        return Performance(
            expected_power_kw=expected,
            actual_power_kw=actual,
            delta_kw=delta,
            relative_deviation=None,
            eligible=False,
            is_breach=False,
        )
    rel = relative_deviation(expected, actual)
    return Performance(
        expected_power_kw=expected,
        actual_power_kw=actual,
        delta_kw=delta,
        relative_deviation=rel,
        eligible=True,
        is_breach=rel >= threshold,
    )


# Re-export interval hours so callers do not hard-code 0.25.
ENERGY_INTERVAL_HOURS = INTERVAL_HOURS
