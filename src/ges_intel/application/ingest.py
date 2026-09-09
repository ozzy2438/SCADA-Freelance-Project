"""Ingest use case: persist telemetry, score expected power, open/resolve alerts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from ges_intel.application.ports import AlertRepo, InverterRepo, ModelPort, TelemetryRepo
from ges_intel.domain.alerting import (
    align_slot,
    evaluate_streak,
    should_open_alert,
    should_resolve_alert,
)
from ges_intel.domain.constants import INTERVAL_MINUTES, TARIFF_USD_PER_KWH
from ges_intel.domain.delta import cash_loss_usd, evaluate_performance, lost_energy_kwh
from ges_intel.domain.errors import ModelNotLoadedError, PersistenceError, UnknownInverterError
from ges_intel.domain.models import Alert, AlertPriority, AlertStatus, TelemetryRecord
from ges_intel.infrastructure.generator import local_hour
from ges_intel.logging_config import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class SensorReading:
    inverter_id: str
    ts_utc: datetime
    irradiance_wm2: float
    temperature_c: float
    actual_power_kw: float


@dataclass
class IngestResult:
    persisted: int
    alerts_opened: int
    alerts_resolved: int
    alerts_updated: int
    inverter_ids: list[str]


def _lost_kwh(records: list[TelemetryRecord]) -> float:
    return sum(lost_energy_kwh(r.expected_power_kw, r.actual_power_kw) for r in records)


def _window_records(
    by_ts: dict[datetime, TelemetryRecord], start: datetime, end: datetime
) -> list[TelemetryRecord]:
    return [row for ts, row in sorted(by_ts.items()) if start <= ts <= end]


def ingest_readings(
    readings: list[SensorReading],
    *,
    inverter_repo: InverterRepo,
    telemetry_repo: TelemetryRepo,
    alert_repo: AlertRepo,
    model: ModelPort,
    tariff_usd_per_kwh: float = TARIFF_USD_PER_KWH,
    now: datetime | None = None,
) -> IngestResult:
    if not readings:
        return IngestResult(0, 0, 0, 0, [])

    unknown: list[str] = []
    inverters = {}
    for reading in readings:
        inv = inverter_repo.get(reading.inverter_id)
        if inv is None:
            unknown.append(reading.inverter_id)
        else:
            inverters[reading.inverter_id] = inv
    if unknown:
        raise UnknownInverterError(sorted(set(unknown)))

    try:
        _ = model.version
        model.predict(0.0, 0.0, 0.0, 1.0)
    except ModelNotLoadedError:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise ModelNotLoadedError() from exc

    records: list[TelemetryRecord] = []
    for reading in readings:
        inv = inverters[reading.inverter_id]
        ts = align_slot(reading.ts_utc)
        hour = local_hour(ts, inv.timezone)
        expected = model.predict(
            reading.irradiance_wm2, reading.temperature_c, hour, inv.capacity_kw
        )
        perf = evaluate_performance(
            reading.actual_power_kw,
            expected,
            reading.irradiance_wm2,
            inv.capacity_kw,
        )
        records.append(
            TelemetryRecord(
                inverter_id=inv.id,
                ts_utc=ts,
                irradiance_wm2=reading.irradiance_wm2,
                temperature_c=reading.temperature_c,
                actual_power_kw=perf.actual_power_kw,
                expected_power_kw=perf.expected_power_kw,
                delta_kw=perf.delta_kw,
                relative_deviation=perf.relative_deviation,
                eligible=perf.eligible,
                is_breach=perf.is_breach,
            )
        )

    try:
        persisted = telemetry_repo.upsert_many(records)
    except Exception as exc:
        logger.exception("duckdb_write_failed")
        raise PersistenceError("duckdb", str(exc)) from exc
    logger.info("ingest_persisted count=%s inverters=%s", persisted, sorted(inverters))

    opened = resolved = updated = 0
    by_inv: dict[str, list[TelemetryRecord]] = {}
    for rec in records:
        by_inv.setdefault(rec.inverter_id, []).append(rec)

    created_at = now or datetime.now(timezone.utc)
    created_at = created_at if created_at.tzinfo else created_at.replace(tzinfo=timezone.utc)

    lookback = timedelta(minutes=INTERVAL_MINUTES * 3)
    for inv_id, recs in by_inv.items():
        recs.sort(key=lambda r: r.ts_utc)
        window_start = recs[0].ts_utc - lookback
        window_end = recs[-1].ts_utc
        try:
            history = telemetry_repo.get_window(inv_id, window_start, window_end)
        except Exception as exc:
            logger.exception("duckdb_read_failed")
            raise PersistenceError("duckdb", str(exc)) from exc
        by_ts = {align_slot(row.ts_utc): row for row in history}
        for rec in recs:
            by_ts[rec.ts_utc] = rec

        try:
            open_alert = alert_repo.get_open(inv_id)
        except Exception as exc:
            logger.exception("postgres_read_failed")
            raise PersistenceError("postgres", str(exc)) from exc

        for rec in recs:
            streak = evaluate_streak(by_ts, rec.ts_utc)
            try:
                if should_open_alert(streak):
                    start = streak.slots[0]
                    end = streak.slots[-1]
                    slot_rows = _window_records(by_ts, start, end)
                    lost = _lost_kwh(slot_rows)
                    cash = cash_loss_usd(lost, tariff_usd_per_kwh)
                    if open_alert is None:
                        open_alert = Alert(
                            inverter_id=inv_id,
                            status=AlertStatus.OPEN,
                            priority=AlertPriority.HIGH,
                            window_start=start,
                            window_end=end,
                            streak_periods=len(streak.slots),
                            lost_kwh=round(lost, 4),
                            cash_loss_usd=round(cash, 4),
                            model_version=model.version,
                            created_at=created_at,
                        )
                        open_alert = alert_repo.insert(open_alert)
                        opened += 1
                        logger.info(
                            "alert_opened inverter_id=%s window_start=%s window_end=%s lost_kwh=%s",
                            inv_id,
                            start.isoformat(),
                            end.isoformat(),
                            lost,
                        )
                    else:
                        open_alert.window_end = end
                        all_rows = _window_records(by_ts, open_alert.window_start, end)
                        lost = _lost_kwh(all_rows)
                        open_alert.lost_kwh = round(lost, 4)
                        open_alert.cash_loss_usd = round(
                            cash_loss_usd(lost, tariff_usd_per_kwh), 4
                        )
                        open_alert.streak_periods = max(open_alert.streak_periods, len(streak.slots))
                        alert_repo.update(open_alert)
                        updated += 1
                elif should_resolve_alert(streak) and open_alert is not None:
                    open_alert.status = AlertStatus.RESOLVED
                    open_alert.resolved_at = rec.ts_utc
                    open_alert.window_end = rec.ts_utc
                    alert_repo.update(open_alert)
                    logger.info(
                        "alert_resolved inverter_id=%s resolved_at=%s",
                        inv_id,
                        rec.ts_utc.isoformat(),
                    )
                    resolved += 1
                    open_alert = None
            except PersistenceError:
                raise
            except Exception as exc:
                logger.exception("postgres_write_failed")
                raise PersistenceError("postgres", str(exc)) from exc

    return IngestResult(
        persisted=persisted,
        alerts_opened=opened,
        alerts_resolved=resolved,
        alerts_updated=updated,
        inverter_ids=sorted(inverters),
    )
