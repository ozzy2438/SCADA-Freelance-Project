"""Ports (interfaces) for persistence and the expected-power model."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from ges_intel.domain.models import Alert, AlertStatus, Inverter, TelemetryRecord


class InverterRepo(Protocol):
    def get(self, inverter_id: str) -> Inverter | None: ...

    def list_all(self) -> list[Inverter]: ...

    def upsert(self, inverter: Inverter) -> None: ...


class TelemetryRepo(Protocol):
    def upsert_many(self, rows: list[TelemetryRecord]) -> int: ...

    def get_window(
        self, inverter_id: str, start: datetime, end: datetime
    ) -> list[TelemetryRecord]: ...

    def iter_range(self, start: datetime, end: datetime) -> list[TelemetryRecord]: ...

    def count(self) -> int: ...

    def time_range(self) -> tuple[datetime, datetime] | None: ...


class AlertRepo(Protocol):
    def get_open(self, inverter_id: str) -> Alert | None: ...

    def insert(self, alert: Alert) -> Alert: ...

    def update(self, alert: Alert) -> None: ...

    def list_alerts(
        self, status: AlertStatus | str | None = None, limit: int = 200
    ) -> list[Alert]: ...

    def count_open(self) -> int: ...


class ModelPort(Protocol):
    version: str

    def predict(
        self,
        irradiance_wm2: float,
        temperature_c: float,
        hour_of_day: float,
        capacity_kw: float,
    ) -> float: ...
