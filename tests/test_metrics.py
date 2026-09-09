"""Metrics range and energy aggregation tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ges_intel.application.metrics import build_performance_report
from ges_intel.domain.errors import MetricsRangeError
from ges_intel.domain.models import TelemetryRecord
from ges_intel.infrastructure.duckdb_store import DuckDBTelemetryRepo
from ges_intel.infrastructure.postgres import (
    SqlAlchemyAlertRepo,
    create_engine_from_url,
    create_session_factory,
    init_schema,
)


class _EmptyAlerts:
    def count_open(self) -> int:
        return 0


def test_metrics_rejects_inverted_and_wide_range(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite+pysqlite:///{tmp_path}/ges.db")
    init_schema(engine)
    alerts = SqlAlchemyAlertRepo(create_session_factory(engine))
    telemetry = DuckDBTelemetryRepo(str(tmp_path / "t.duckdb"))
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(MetricsRangeError):
        build_performance_report(
            telemetry_repo=telemetry,
            alert_repo=alerts,
            start=start + timedelta(days=2),
            end=start,
        )
    with pytest.raises(MetricsRangeError):
        build_performance_report(
            telemetry_repo=telemetry,
            alert_repo=alerts,
            start=start,
            end=start + timedelta(days=40),
        )


def test_empty_window_health_is_100(tmp_path: Path) -> None:
    engine = create_engine_from_url(f"sqlite+pysqlite:///{tmp_path}/ges.db")
    init_schema(engine)
    alerts = SqlAlchemyAlertRepo(create_session_factory(engine))
    telemetry = DuckDBTelemetryRepo(str(tmp_path / "t.duckdb"))
    start = datetime(2026, 6, 1, tzinfo=timezone.utc)
    report = build_performance_report(
        telemetry_repo=telemetry,
        alert_repo=alerts,
        start=start,
        end=start + timedelta(days=1),
    )
    assert report.plant.health_pct == 100.0
    assert report.plant.actual_kwh == 0.0


def test_daily_buckets_use_plant_timezone(tmp_path: Path) -> None:
    telemetry = DuckDBTelemetryRepo(str(tmp_path / "t.duckdb"))
    ts = datetime(2026, 6, 15, 21, 0, tzinfo=timezone.utc)  # 00:00 next day in Istanbul
    telemetry.upsert_many(
        [
            TelemetryRecord(
                inverter_id="INV-01",
                ts_utc=ts,
                irradiance_wm2=0.0,
                temperature_c=20.0,
                actual_power_kw=0.0,
                expected_power_kw=0.0,
                delta_kw=0.0,
                relative_deviation=None,
                eligible=False,
                is_breach=False,
            )
        ]
    )
    report = build_performance_report(
        telemetry_repo=telemetry,
        alert_repo=_EmptyAlerts(),
        start=ts,
        end=ts + timedelta(minutes=15),
        plant_timezone="Europe/Istanbul",
    )
    assert report.daily[0].date == "2026-06-16"
