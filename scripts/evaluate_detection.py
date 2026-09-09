"""Measure detection quality against the simulator's hidden ground-truth labels.

Replays labeled synthetic telemetry through the real ingest pipeline and reports
soiling-day recall, false alarms on clean days, night false positives, detection
latency, and the energy/cash the alerts account for. Ground-truth labels are used
only for scoring; they never reach the model or the API.

Usage:
    python scripts/evaluate_detection.py --n-days 14 --seed 42 --json report.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import tempfile
import time
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from ges_intel.application.ingest import SensorReading, ingest_readings
from ges_intel.application.metrics import build_performance_report
from ges_intel.application.train import train_expected_model
from ges_intel.domain.constants import (
    DEFAULT_N_DAYS,
    DEFAULT_SEED,
    INTERVAL_HOURS,
    IRRADIANCE_FLOOR_WM2,
    PLANT_TIMEZONE,
    TARIFF_USD_PER_KWH,
)
from ges_intel.domain.models import AlertStatus, GeneratedReading
from ges_intel.infrastructure.duckdb_store import DuckDBTelemetryRepo
from ges_intel.infrastructure.generator import DEFAULT_FLEET, generate_readings
from ges_intel.infrastructure.ml_model import RandomForestExpectedModel
from ges_intel.infrastructure.postgres import (
    SqlAlchemyAlertRepo,
    SqlAlchemyInverterRepo,
    create_engine_from_url,
    create_session_factory,
    init_schema,
)
from ges_intel.logging_config import configure_logging


def _local_day(ts: datetime, tz_name: str = PLANT_TIMEZONE) -> str:
    return ts.astimezone(ZoneInfo(tz_name)).date().isoformat()


def _truth_sets(readings: list[GeneratedReading]) -> tuple[set, set]:
    """Return (soiling inverter-days, all inverter-days)."""
    soiling: set[tuple[str, str]] = set()
    all_days: set[tuple[str, str]] = set()
    for r in readings:
        key = (r.inverter_id, _local_day(r.ts_utc))
        all_days.add(key)
        if r.anomaly_type == "soiling":
            soiling.add(key)
    return soiling, all_days


def _detection_latencies_min(rows: list) -> list[float]:
    """Minutes from the first eligible under-performing slot to the slot that completes
    a four-slot streak, computed from persisted telemetry only."""
    by_inv: dict[str, list] = defaultdict(list)
    for row in rows:
        by_inv[row.inverter_id].append(row)

    latencies: list[float] = []
    for series in by_inv.values():
        series.sort(key=lambda r: r.ts_utc)
        streak: list[datetime] = []
        for row in series:
            if not row.eligible:
                streak = []
                continue
            if not row.is_breach:
                streak = []
                continue
            if streak and (row.ts_utc - streak[-1]) != timedelta(minutes=15):
                streak = []
            streak.append(row.ts_utc)
            if len(streak) == 4:
                latencies.append((streak[-1] - streak[0]).total_seconds() / 60.0)
    return latencies


def evaluate(n_days: int, seed: int, workdir: Path) -> dict:
    readings = generate_readings(seed=seed, n_days=n_days)
    soiling_days, all_days = _truth_sets(readings)

    engine = create_engine_from_url(f"sqlite+pysqlite:///{workdir}/eval.db")
    init_schema(engine)
    factory = create_session_factory(engine)
    inverter_repo = SqlAlchemyInverterRepo(factory)
    alert_repo = SqlAlchemyAlertRepo(factory)
    telemetry_repo = DuckDBTelemetryRepo(str(workdir / "eval.duckdb"))
    for inverter in DEFAULT_FLEET:
        inverter_repo.upsert(inverter)

    model_path = workdir / "model.joblib"
    meta_path = workdir / "model_metadata.json"
    train_meta = train_expected_model(readings, model_path, meta_path)
    model = RandomForestExpectedModel(str(model_path), str(meta_path))
    model.load()

    ingest_started = time.perf_counter()
    batch = 400
    for i in range(0, len(readings), batch):
        chunk = readings[i : i + batch]
        ingest_readings(
            [
                SensorReading(
                    inverter_id=r.inverter_id,
                    ts_utc=r.ts_utc,
                    irradiance_wm2=r.irradiance_wm2,
                    temperature_c=r.temperature_c,
                    actual_power_kw=r.actual_power_kw,
                )
                for r in chunk
            ],
            inverter_repo=inverter_repo,
            telemetry_repo=telemetry_repo,
            alert_repo=alert_repo,
            model=model,
        )
    ingest_seconds = time.perf_counter() - ingest_started

    alerts = alert_repo.list_alerts(limit=500)
    rows = telemetry_repo.iter_range(
        min(r.ts_utc for r in readings), max(r.ts_utc for r in readings) + timedelta(minutes=15)
    )

    # An inverter-day counts as covered when an alert window overlaps any part of it.
    covered_days: set[tuple[str, str]] = set()
    night_alerts = 0
    opened_on_soiling_day = 0
    for alert in alerts:
        window_rows = telemetry_repo.get_window(
            alert.inverter_id, alert.window_start, alert.window_end
        )
        for row in window_rows:
            covered_days.add((alert.inverter_id, _local_day(row.ts_utc)))
        if window_rows and all(r.irradiance_wm2 < IRRADIANCE_FLOOR_WM2 for r in window_rows):
            night_alerts += 1
        if (alert.inverter_id, _local_day(alert.window_start)) in soiling_days:
            opened_on_soiling_day += 1

    latencies_min = _detection_latencies_min(rows)
    detected_soiling = covered_days & soiling_days
    false_alarm_days = covered_days - soiling_days
    clean_days = all_days - soiling_days
    night_rows = [r for r in rows if r.irradiance_wm2 < IRRADIANCE_FLOOR_WM2]
    night_breaches = sum(1 for r in night_rows if r.is_breach)

    truth_lost_kwh = sum(
        max(0.0, r.true_expected_kw - r.actual_power_kw) * INTERVAL_HOURS
        for r in readings
        if r.is_anomaly
    )

    metrics_started = time.perf_counter()
    report = build_performance_report(
        telemetry_repo=telemetry_repo,
        alert_repo=alert_repo,
        start=min(r.ts_utc for r in readings),
        end=max(r.ts_utc for r in readings) + timedelta(minutes=15),
    )
    metrics_seconds = time.perf_counter() - metrics_started

    recall = len(detected_soiling) / len(soiling_days) if soiling_days else 0.0
    precision = len(detected_soiling) / len(covered_days) if covered_days else 0.0

    return {
        "config": {
            "seed": seed,
            "n_days": n_days,
            "inverters": len(DEFAULT_FLEET),
            "telemetry_rows": len(readings),
            "tariff_usd_per_kwh": TARIFF_USD_PER_KWH,
        },
        "model": {
            "rmse_model_kw": round(train_meta["rmse_model"], 4),
            "rmse_physics_kw": round(train_meta["rmse_physics"], 4),
            "improvement_pct": round(
                100.0 * (train_meta["rmse_physics"] - train_meta["rmse_model"]) / train_meta["rmse_physics"],
                2,
            ),
            "clean_training_rows": train_meta["n_clean_rows"],
        },
        "detection": {
            "soiling_inverter_days": len(soiling_days),
            "covered_soiling_inverter_days": len(detected_soiling),
            "recall_inverter_days": round(recall, 4),
            "precision_inverter_days": round(precision, 4),
            "clean_inverter_days": len(clean_days),
            "false_alarm_inverter_days": len(false_alarm_days),
            "night_rows": len(night_rows),
            "night_breach_rows": night_breaches,
            "night_alerts": night_alerts,
            "alerts_total": len(alerts),
            "alerts_open": sum(1 for a in alerts if a.status is AlertStatus.OPEN),
            "alerts_opened_on_soiling_day": opened_on_soiling_day,
            "alerts_opened_on_clean_day": len(alerts) - opened_on_soiling_day,
            "detection_latency_min_median": round(statistics.median(latencies_min), 1)
            if latencies_min
            else None,
            "detection_latency_min_max": round(max(latencies_min), 1) if latencies_min else None,
            "streaks_fired": len(latencies_min),
        },
        "money": {
            "ground_truth_lost_kwh": round(truth_lost_kwh, 2),
            "ground_truth_lost_usd": round(truth_lost_kwh * TARIFF_USD_PER_KWH, 2),
            "alerted_lost_kwh": round(sum(a.lost_kwh for a in alerts), 2),
            "alerted_cash_loss_usd": round(sum(a.cash_loss_usd for a in alerts), 2),
            "plant_actual_kwh": report.plant.actual_kwh,
            "plant_expected_kwh": report.plant.expected_kwh,
            "plant_health_pct": report.plant.health_pct,
        },
        "performance": {
            "ingest_seconds_total": round(ingest_seconds, 2),
            "ingest_rows_per_second": round(len(readings) / ingest_seconds, 1),
            "metrics_query_seconds": round(metrics_seconds, 4),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate GES detection against ground truth")
    parser.add_argument("--n-days", type=int, default=DEFAULT_N_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--json", default="")
    args = parser.parse_args(argv)

    configure_logging("WARNING")
    with tempfile.TemporaryDirectory() as tmp:
        result = evaluate(args.n_days, args.seed, Path(tmp))
    text = json.dumps(result, indent=2)
    print(text)
    if args.json:
        Path(args.json).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
