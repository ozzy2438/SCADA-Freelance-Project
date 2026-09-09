"""Ingest use-case tests: unique telemetry key, unknown inverter, rising-edge alerts."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from ges_intel.application.ingest import SensorReading, ingest_readings
from ges_intel.application.metrics import build_performance_report
from ges_intel.domain.errors import UnknownInverterError
from ges_intel.domain.models import AlertStatus
from ges_intel.infrastructure.duckdb_store import DuckDBTelemetryRepo
from ges_intel.infrastructure.generator import DEFAULT_FLEET
from ges_intel.infrastructure.ml_model import PhysicsExpectedModel
from ges_intel.infrastructure.postgres import (
    SqlAlchemyAlertRepo,
    SqlAlchemyInverterRepo,
    create_engine_from_url,
    create_session_factory,
    init_schema,
)


def _repos(tmp_path: Path):
    engine = create_engine_from_url(f"sqlite+pysqlite:///{tmp_path}/ges.db")
    init_schema(engine)
    factory = create_session_factory(engine)
    inverters = SqlAlchemyInverterRepo(factory)
    alerts = SqlAlchemyAlertRepo(factory)
    telemetry = DuckDBTelemetryRepo(str(tmp_path / "t.duckdb"))
    for inv in DEFAULT_FLEET:
        inverters.upsert(inv)
    return inverters, alerts, telemetry


def _reading(ts: datetime, actual: float, inverter_id: str = "INV-01") -> SensorReading:
    return SensorReading(
        inverter_id=inverter_id,
        ts_utc=ts,
        irradiance_wm2=850.0,
        temperature_c=25.0,
        actual_power_kw=actual,
    )


class TestIngest:
    def test_unknown_inverter_rejected_before_write(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        ts = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        with pytest.raises(UnknownInverterError):
            ingest_readings(
                [_reading(ts, 10.0, inverter_id="INV-99")],
                inverter_repo=inverters,
                telemetry_repo=telemetry,
                alert_repo=alerts,
                model=PhysicsExpectedModel(),
            )
        assert telemetry.count() == 0

    def test_duplicate_timestamp_upserts(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        ts = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        ingest_readings(
            [_reading(ts, 40.0)],
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        ingest_readings(
            [_reading(ts, 41.0)],
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert telemetry.count() == 1
        row = telemetry.get_window("INV-01", ts, ts)[0]
        assert row.actual_power_kw == pytest.approx(41.0)

    def test_four_breaches_open_one_alert_and_reingest_is_idempotent(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        start = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        batch = [_reading(start + timedelta(minutes=15 * i), 40.0) for i in range(4)]
        first = ingest_readings(
            batch,
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert first.alerts_opened == 1
        assert alerts.count_open() == 1
        second = ingest_readings(
            batch,
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert second.alerts_opened == 0
        assert alerts.count_open() == 1
        open_alert = alerts.get_open("INV-01")
        assert open_alert is not None
        assert open_alert.status is AlertStatus.OPEN
        assert open_alert.priority.value == "high"
        assert open_alert.lost_kwh > 0
        assert open_alert.cash_loss_usd == pytest.approx(open_alert.lost_kwh * 0.10)

    def test_three_breaches_do_not_alert(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        start = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        batch = [_reading(start + timedelta(minutes=15 * i), 40.0) for i in range(3)]
        result = ingest_readings(
            batch,
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert result.alerts_opened == 0
        assert alerts.count_open() == 0

    def test_gap_does_not_count_as_consecutive(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        start = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        times = [start, start + timedelta(minutes=15), start + timedelta(minutes=45), start + timedelta(minutes=60)]
        result = ingest_readings(
            [_reading(ts, 40.0) for ts in times],
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert result.alerts_opened == 0

    def test_night_points_do_not_open_alerts(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        start = datetime(2026, 6, 15, 0, 0, tzinfo=timezone.utc)
        batch = [
            SensorReading("INV-01", start + timedelta(minutes=15 * i), 0.0, 15.0, 0.0)
            for i in range(4)
        ]
        result = ingest_readings(
            batch,
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert result.alerts_opened == 0

    def test_four_healthy_slots_resolve_open_alert(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        start = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        bad = [_reading(start + timedelta(minutes=15 * i), 40.0) for i in range(4)]
        ingest_readings(bad, inverter_repo=inverters, telemetry_repo=telemetry, alert_repo=alerts, model=PhysicsExpectedModel())
        assert alerts.count_open() == 1
        healthy_start = start + timedelta(minutes=60)
        good = [_reading(healthy_start + timedelta(minutes=15 * i), 80.0) for i in range(4)]
        result = ingest_readings(
            good,
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        assert result.alerts_resolved == 1
        assert alerts.count_open() == 0
        resolved = alerts.list_alerts(status="resolved")
        assert len(resolved) == 1
        assert resolved[0].resolved_at is not None

    def test_metrics_use_quarter_hour_energy(self, tmp_path: Path) -> None:
        inverters, alerts, telemetry = _repos(tmp_path)
        ts = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
        ingest_readings(
            [_reading(ts, 40.0)],
            inverter_repo=inverters,
            telemetry_repo=telemetry,
            alert_repo=alerts,
            model=PhysicsExpectedModel(),
        )
        report = build_performance_report(
            telemetry_repo=telemetry,
            alert_repo=alerts,
            start=ts,
            end=ts + timedelta(minutes=15),
        )
        assert report.plant.actual_kwh == pytest.approx(10.0)
        assert report.plant.expected_kwh == pytest.approx(report.inverters[0].expected_kwh)
        assert report.plant.cash_loss_usd == pytest.approx(report.plant.cash_loss_usd)
