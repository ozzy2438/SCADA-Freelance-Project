"""Pydantic request/response schemas."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class SensorReadingIn(BaseModel):
    inverter_id: str = Field(min_length=1, max_length=32)
    timestamp: datetime
    irradiance_wm2: float = Field(ge=0, le=1500)
    temperature_c: float = Field(ge=-30, le=85)
    actual_power_kw: float = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def ensure_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)


class IngestRequest(BaseModel):
    readings: list[SensorReadingIn] = Field(min_length=1, max_length=500)


class IngestResponse(BaseModel):
    persisted: int
    alerts_opened: int
    alerts_resolved: int
    alerts_updated: int
    inverter_ids: list[str]


class AlertOut(BaseModel):
    id: str | None
    inverter_id: str
    status: str
    priority: str
    window_start: datetime
    window_end: datetime
    streak_periods: int
    lost_kwh: float
    cash_loss_usd: float
    model_version: str
    created_at: datetime
    resolved_at: datetime | None = None


class InverterMetricsOut(BaseModel):
    inverter_id: str
    actual_kwh: float
    expected_kwh: float
    mean_relative_deviation: float | None
    cash_loss_usd: float
    eligible_periods: int


class DailyPointOut(BaseModel):
    date: str
    actual_kwh: float
    expected_kwh: float


class PlantMetricsOut(BaseModel):
    actual_kwh: float
    expected_kwh: float
    mean_relative_deviation: float | None
    health_pct: float
    cash_loss_usd: float
    open_alerts: int


class PerformanceMetricsOut(BaseModel):
    start: datetime
    end: datetime
    plant: PlantMetricsOut
    inverters: list[InverterMetricsOut]
    daily: list[DailyPointOut]


class ErrorOut(BaseModel):
    detail: str
    code: Literal["unknown_inverter", "model_not_loaded", "db_unavailable", "bad_range"] | None = None
