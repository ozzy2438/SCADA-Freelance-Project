"""Deterministic 15-minute SCADA simulator with labeled soiling and shading."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np

from ges_intel.domain.constants import (
    DEFAULT_N_DAYS,
    DEFAULT_SEED,
    DEFAULT_START_DATE,
    INTERVAL_MINUTES,
    IRRADIANCE_FLOOR_WM2,
    PLANT_LATITUDE,
    PLANT_LONGITUDE,
    PLANT_TIMEZONE,
    SHADING_REMAINING_MAX,
    SHADING_REMAINING_MIN,
    SOILING_MAX,
    SOILING_MIN,
)
from ges_intel.domain.models import GeneratedReading, Inverter
from ges_intel.domain.physics import clip_power_kw, generator_true_expected_kw

SLOTS_PER_DAY = 24 * 60 // INTERVAL_MINUTES


DEFAULT_FLEET: tuple[Inverter, ...] = (
    Inverter("INV-01", "Block A — 100 kW", 100.0, PLANT_LATITUDE, PLANT_LONGITUDE, PLANT_TIMEZONE),
    Inverter("INV-02", "Block B — 150 kW", 150.0, PLANT_LATITUDE + 0.002, PLANT_LONGITUDE, PLANT_TIMEZONE),
    Inverter("INV-03", "Block C — 80 kW", 80.0, PLANT_LATITUDE, PLANT_LONGITUDE + 0.002, PLANT_TIMEZONE),
    Inverter("INV-04", "Block D — 200 kW", 200.0, PLANT_LATITUDE - 0.002, PLANT_LONGITUDE, PLANT_TIMEZONE),
    Inverter("INV-05", "Block E — 120 kW", 120.0, PLANT_LATITUDE, PLANT_LONGITUDE - 0.002, PLANT_TIMEZONE),
)


def parse_start_date(value: str) -> datetime:
    year, month, day = (int(p) for p in value.split("-"))
    return datetime(year, month, day, tzinfo=timezone.utc)


def clear_sky_ghi_wm2(ts_utc: datetime, latitude: float) -> float:
    """Haurwitz-like clear-sky GHI from solar elevation (no external weather API)."""
    local = ts_utc.astimezone(ZoneInfo(PLANT_TIMEZONE))
    doy = local.timetuple().tm_yday
    decl = math.radians(23.45 * math.sin(math.radians(360.0 / 365.0 * (284 + doy))))
    lat = math.radians(latitude)
    hour = local.hour + local.minute / 60.0 + local.second / 3600.0
    hour_angle = math.radians(15.0 * (hour - 12.0))
    sin_elev = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(hour_angle)
    sin_elev = max(-1.0, min(1.0, sin_elev))
    if sin_elev <= 0:
        return 0.0
    air_mass = 1.0 / max(sin_elev, 0.04)
    ghi = 1361.0 * (0.7 ** (air_mass**0.678)) * sin_elev
    return float(min(ghi, 1100.0))


def local_hour(ts_utc: datetime, tz_name: str = PLANT_TIMEZONE) -> float:
    local = ts_utc.astimezone(ZoneInfo(tz_name))
    return local.hour + local.minute / 60.0 + local.second / 3600.0


def _soiling_schedule(
    rng: np.random.Generator,
    inverter_ids: list[str],
    days: list[datetime],
) -> dict[tuple[str, datetime], float]:
    """Map (inverter_id, local-day-midnight) -> soiling fraction in [0.12, 0.15]."""
    schedule: dict[tuple[str, datetime], float] = {}
    n_days = max(3, len(days) // 8)
    for inv_id in inverter_ids:
        chosen = rng.choice(np.arange(len(days)), size=min(n_days, len(days)), replace=False)
        for idx in chosen:
            fraction = float(rng.uniform(SOILING_MIN, SOILING_MAX))
            schedule[(inv_id, days[int(idx)])] = fraction
    return schedule


def _shading_events(
    rng: np.random.Generator,
    inverter_ids: list[str],
    timestamps: list[datetime],
    n_events: int = 12,
) -> dict[tuple[str, datetime], float]:
    """Short 1–3 slot remaining-power factors (sudden shading)."""
    events: dict[tuple[str, datetime], float] = {}
    daylight = [t for t in timestamps if t.astimezone(ZoneInfo(PLANT_TIMEZONE)).hour in range(8, 17)]
    if not daylight:
        return events
    for _ in range(n_events):
        inv_id = str(rng.choice(inverter_ids))
        start = daylight[int(rng.integers(0, len(daylight)))]
        duration = int(rng.integers(1, 4))
        remaining = float(rng.uniform(SHADING_REMAINING_MIN, SHADING_REMAINING_MAX))
        for k in range(duration):
            ts = start + timedelta(minutes=INTERVAL_MINUTES * k)
            events[(inv_id, ts)] = remaining
    return events


def generate_readings(
    *,
    seed: int = DEFAULT_SEED,
    n_days: int = DEFAULT_N_DAYS,
    start: str | datetime = DEFAULT_START_DATE,
    fleet: tuple[Inverter, ...] | list[Inverter] | None = None,
) -> list[GeneratedReading]:
    """Produce 15-minute telemetry with hidden ground-truth labels.

    ``is_anomaly`` / ``true_expected_kw`` / ``anomaly_type`` are generator-only
    and must never be sent on the public ingest API as features.
    """
    rng = np.random.default_rng(seed)
    inverters = list(fleet) if fleet is not None else list(DEFAULT_FLEET)
    if isinstance(start, str):
        start_utc = parse_start_date(start)
    else:
        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
    start_utc = start_utc.replace(hour=0, minute=0, second=0, microsecond=0)

    timestamps: list[datetime] = []
    for day in range(n_days):
        for slot in range(SLOTS_PER_DAY):
            timestamps.append(start_utc + timedelta(days=day, minutes=slot * INTERVAL_MINUTES))

    tz = ZoneInfo(PLANT_TIMEZONE)
    unique_days = []
    seen = set()
    for ts in timestamps:
        day_key = ts.astimezone(tz).date()
        if day_key not in seen:
            seen.add(day_key)
            unique_days.append(datetime(day_key.year, day_key.month, day_key.day, tzinfo=tz))

    soiling = _soiling_schedule(rng, [inv.id for inv in inverters], unique_days)
    shading = _shading_events(rng, [inv.id for inv in inverters], timestamps)

    rows: list[GeneratedReading] = []
    for inv in inverters:
        for ts in timestamps:
            ghi_clear = clear_sky_ghi_wm2(ts, inv.latitude)
            cloud = float(rng.uniform(0.85, 1.0)) if ghi_clear > 0 else 1.0
            irradiance = max(0.0, ghi_clear * cloud + float(rng.normal(0.0, 8.0 if ghi_clear > 0 else 0.0)))
            if ghi_clear <= 0:
                irradiance = 0.0
            hour = local_hour(ts, inv.timezone)
            temp = 18.0 + 14.0 * min(1.0, irradiance / 900.0) + float(rng.normal(0.0, 0.6))
            true_expected = generator_true_expected_kw(inv.capacity_kw, irradiance, temp, hour)

            local_day = datetime.combine(ts.astimezone(tz).date(), datetime.min.time(), tzinfo=tz)
            soiling_frac = soiling.get((inv.id, local_day))
            shade_remaining = shading.get((inv.id, ts))

            factor = 1.0
            anomaly_type: str | None = None
            is_anomaly = False
            daylight = irradiance >= IRRADIANCE_FLOOR_WM2 and true_expected > 0
            if daylight and soiling_frac is not None:
                factor *= 1.0 - soiling_frac
                is_anomaly = True
                anomaly_type = "soiling"
            if daylight and shade_remaining is not None:
                factor *= shade_remaining
                is_anomaly = True
                if anomaly_type is None:
                    anomaly_type = "shading"

            noise = 1.0 + float(rng.normal(0.0, 0.012)) if daylight else 1.0
            actual = clip_power_kw(true_expected * factor * noise, inv.capacity_kw)
            if not daylight:
                is_anomaly = False
                anomaly_type = None
                actual = 0.0

            rows.append(
                GeneratedReading(
                    inverter_id=inv.id,
                    ts_utc=ts,
                    irradiance_wm2=round(irradiance, 3),
                    temperature_c=round(temp, 3),
                    actual_power_kw=round(actual, 4),
                    true_expected_kw=round(true_expected, 4),
                    is_anomaly=is_anomaly,
                    anomaly_type=anomaly_type,
                    capacity_kw=inv.capacity_kw,
                )
            )
    return rows
