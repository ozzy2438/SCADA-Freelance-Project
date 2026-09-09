"""Performance-metrics aggregation from DuckDB telemetry + Postgres alerts."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ges_intel.application.ports import AlertRepo, TelemetryRepo
from ges_intel.domain.alerting import to_utc
from ges_intel.domain.constants import PLANT_TIMEZONE, TARIFF_USD_PER_KWH
from ges_intel.domain.delta import cash_loss_usd, lost_energy_kwh, power_to_energy_kwh
from ges_intel.domain.errors import MetricsRangeError
from ges_intel.domain.models import AlertStatus, TelemetryRecord


@dataclass
class InverterMetrics:
    inverter_id: str
    actual_kwh: float
    expected_kwh: float
    mean_relative_deviation: float | None
    cash_loss_usd: float
    eligible_periods: int


@dataclass
class DailyPoint:
    date: str
    actual_kwh: float
    expected_kwh: float


@dataclass
class PlantMetrics:
    actual_kwh: float
    expected_kwh: float
    mean_relative_deviation: float | None
    health_pct: float
    cash_loss_usd: float
    open_alerts: int


@dataclass
class PerformanceReport:
    start: datetime
    end: datetime
    plant: PlantMetrics
    inverters: list[InverterMetrics] = field(default_factory=list)
    daily: list[DailyPoint] = field(default_factory=list)


def _mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _group_key(record: TelemetryRecord, tz_name: str) -> str:
    local = to_utc(record.ts_utc).astimezone(ZoneInfo(tz_name))
    return local.date().isoformat()


def build_performance_report(
    *,
    telemetry_repo: TelemetryRepo,
    alert_repo: AlertRepo,
    start: datetime,
    end: datetime,
    tariff_usd_per_kwh: float = TARIFF_USD_PER_KWH,
    max_span_days: int = 31,
    plant_timezone: str = PLANT_TIMEZONE,
) -> PerformanceReport:
    start = to_utc(start)
    end = to_utc(end)
    if end <= start:
        raise MetricsRangeError("end must be after start")
    if end - start > timedelta(days=max_span_days):
        raise MetricsRangeError(f"range exceeds {max_span_days} days")

    rows = telemetry_repo.iter_range(start, end)
    by_inv: dict[str, list[TelemetryRecord]] = {}
    for row in rows:
        by_inv.setdefault(row.inverter_id, []).append(row)

    inverter_metrics: list[InverterMetrics] = []
    plant_actual = plant_expected = plant_loss = 0.0
    plant_rels: list[float] = []
    daily_acc: dict[str, list[float]] = {}

    for inv_id, recs in sorted(by_inv.items()):
        actual_kwh = sum(power_to_energy_kwh(r.actual_power_kw) for r in recs)
        expected_kwh = sum(power_to_energy_kwh(r.expected_power_kw) for r in recs)
        rels = [r.relative_deviation for r in recs if r.eligible and r.relative_deviation is not None]
        lost = sum(lost_energy_kwh(r.expected_power_kw, r.actual_power_kw) for r in recs)
        cash = cash_loss_usd(lost, tariff_usd_per_kwh)
        inverter_metrics.append(
            InverterMetrics(
                inverter_id=inv_id,
                actual_kwh=round(actual_kwh, 4),
                expected_kwh=round(expected_kwh, 4),
                mean_relative_deviation=None if not rels else round(float(_mean(rels) or 0.0), 6),
                cash_loss_usd=round(cash, 4),
                eligible_periods=len(rels),
            )
        )
        plant_actual += actual_kwh
        plant_expected += expected_kwh
        plant_loss += lost
        plant_rels.extend(rels)
        for rec in recs:
            day = _group_key(rec, plant_timezone)
            bucket = daily_acc.setdefault(day, [0.0, 0.0])
            bucket[0] += power_to_energy_kwh(rec.actual_power_kw)
            bucket[1] += power_to_energy_kwh(rec.expected_power_kw)

    mean_rel = _mean(plant_rels)
    if plant_expected > 0:
        health = max(0.0, min(100.0, 100.0 * plant_actual / plant_expected))
    else:
        health = 100.0

    daily = [
        DailyPoint(date=day, actual_kwh=round(vals[0], 4), expected_kwh=round(vals[1], 4))
        for day, vals in sorted(daily_acc.items())
    ]

    plant = PlantMetrics(
        actual_kwh=round(plant_actual, 4),
        expected_kwh=round(plant_expected, 4),
        mean_relative_deviation=None if mean_rel is None else round(mean_rel, 6),
        health_pct=round(health, 2),
        cash_loss_usd=round(cash_loss_usd(plant_loss, tariff_usd_per_kwh), 4),
        open_alerts=alert_repo.count_open(),
    )
    return PerformanceReport(start=start, end=end, plant=plant, inverters=inverter_metrics, daily=daily)
