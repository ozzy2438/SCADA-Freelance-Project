"""Physical PV expected-power formula and clipping."""

from __future__ import annotations

from ges_intel.domain.constants import (
    PERFORMANCE_RATIO,
    STC_IRRADIANCE_WM2,
    STC_TEMPERATURE_C,
    TEMP_COEFFICIENT_GAMMA,
)


def clip_power_kw(power_kw: float, capacity_kw: float) -> float:
    """Clip power to the physical inverter envelope [0, nameplate]."""
    if capacity_kw < 0:
        raise ValueError("capacity_kw must be non-negative")
    if power_kw != power_kw:  # NaN
        return 0.0
    return max(0.0, min(float(power_kw), float(capacity_kw)))


def physics_expected_kw(
    capacity_kw: float,
    irradiance_wm2: float,
    temperature_c: float,
    *,
    gamma: float = TEMP_COEFFICIENT_GAMMA,
    performance_ratio: float = PERFORMANCE_RATIO,
) -> float:
    """Simple nameplate model used as the transparent engineering baseline.

    P = capacity * (G / 1000) * (1 + γ (T - 25)) * PR, clipped to [0, capacity].
    Night (G <= 0) yields 0. Negative temperature derate is allowed but output
    is still floored at 0.
    """
    if irradiance_wm2 <= 0:
        return 0.0
    temp_factor = 1.0 + gamma * (temperature_c - STC_TEMPERATURE_C)
    raw = (
        capacity_kw
        * (irradiance_wm2 / STC_IRRADIANCE_WM2)
        * temp_factor
        * performance_ratio
    )
    return clip_power_kw(raw, capacity_kw)


def generator_true_expected_kw(
    capacity_kw: float,
    irradiance_wm2: float,
    temperature_c: float,
    local_hour: float,
) -> float:
    """Richer generating function the simulator uses as ground-truth expected.

    Adds a mild incidence-angle (IAM) derate that the simple physics baseline
    does not include, so a model that sees hour-of-day can beat the baseline.
    """
    base = physics_expected_kw(capacity_kw, irradiance_wm2, temperature_c)
    if base <= 0:
        return 0.0
    iam = 1.0 - 0.12 * abs(local_hour - 12.0) / 6.0
    iam = max(0.75, min(1.0, iam))
    return clip_power_kw(base * iam, capacity_kw)
