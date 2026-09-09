"""Typed domain models."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class AnomalyType(str, Enum):
    SOILING = "soiling"
    SHADING = "shading"


class AlertStatus(str, Enum):
    OPEN = "open"
    RESOLVED = "resolved"


class AlertPriority(str, Enum):
    HIGH = "high"


@dataclass(frozen=True)
class Inverter:
    id: str
    name: str
    capacity_kw: float
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class GeneratedReading:
    inverter_id: str
    ts_utc: datetime
    irradiance_wm2: float
    temperature_c: float
    actual_power_kw: float
    true_expected_kw: float
    is_anomaly: bool
    anomaly_type: str | None
    capacity_kw: float


@dataclass
class Performance:
    expected_power_kw: float
    actual_power_kw: float
    delta_kw: float
    relative_deviation: float | None
    eligible: bool
    is_breach: bool


@dataclass
class TelemetryRecord:
    inverter_id: str
    ts_utc: datetime
    irradiance_wm2: float
    temperature_c: float
    actual_power_kw: float
    expected_power_kw: float
    delta_kw: float
    relative_deviation: float | None
    eligible: bool
    is_breach: bool


@dataclass
class StreakResult:
    complete: bool
    all_breach: bool
    all_healthy: bool
    slots: list[datetime]
    records: list[TelemetryRecord | None]


@dataclass
class Alert:
    inverter_id: str
    status: AlertStatus
    priority: AlertPriority
    window_start: datetime
    window_end: datetime
    streak_periods: int
    lost_kwh: float
    cash_loss_usd: float
    model_version: str
    created_at: datetime
    resolved_at: datetime | None = None
    id: str | None = None
    extra: dict = field(default_factory=dict)
