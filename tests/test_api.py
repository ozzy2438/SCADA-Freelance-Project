"""API contract tests: auth, validation, ingest, alerts, metrics, error codes."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ges_intel.config import Settings
from ges_intel.infrastructure.generator import DEFAULT_FLEET
from ges_intel.interfaces.api.app import create_app


@pytest.fixture
def client(tmp_path: Path):
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/ges.db",
        duckdb_path=str(tmp_path / "t.duckdb"),
        model_path=str(tmp_path / "missing.joblib"),
        model_metadata_path=str(tmp_path / "missing.json"),
        api_key="test-key",
    )
    app = create_app(settings)
    with TestClient(app) as test_client:
        for inv in DEFAULT_FLEET:
            test_client.app.state.inverter_repo.upsert(inv)
        yield test_client


def _headers(key: str = "test-key") -> dict[str, str]:
    return {"X-API-Key": key}


def _payload(actual: float = 40.0, n: int = 4, inverter_id: str = "INV-01") -> dict:
    start = datetime(2026, 6, 15, 9, 0, tzinfo=timezone.utc)
    return {
        "readings": [
            {
                "inverter_id": inverter_id,
                "timestamp": (start + timedelta(minutes=15 * i)).isoformat(),
                "irradiance_wm2": 850.0,
                "temperature_c": 25.0,
                "actual_power_kw": actual,
            }
            for i in range(n)
        ]
    }


class TestAPI:
    def test_health(self, client: TestClient) -> None:
        assert client.get("/health").status_code == 200

    def test_missing_api_key(self, client: TestClient) -> None:
        res = client.get("/alerts")
        assert res.status_code == 401

    def test_wrong_api_key(self, client: TestClient) -> None:
        res = client.get("/alerts", headers=_headers("nope"))
        assert res.status_code == 401

    def test_validation_error_is_422(self, client: TestClient) -> None:
        res = client.post("/ingest", headers=_headers(), json={"readings": []})
        assert res.status_code == 422

    def test_unknown_inverter_is_400(self, client: TestClient) -> None:
        res = client.post("/ingest", headers=_headers(), json=_payload(inverter_id="NOPE"))
        assert res.status_code == 400
        assert res.json()["code"] == "unknown_inverter"

    def test_ingest_opens_alert_and_lists_it(self, client: TestClient) -> None:
        res = client.post("/ingest", headers=_headers(), json=_payload())
        assert res.status_code == 200, res.text
        body = res.json()
        assert body["persisted"] == 4
        assert body["alerts_opened"] == 1
        listed = client.get("/alerts", headers=_headers(), params={"status": "open"})
        assert listed.status_code == 200
        rows = listed.json()
        assert len(rows) == 1
        assert rows[0]["inverter_id"] == "INV-01"
        assert rows[0]["priority"] == "high"
        assert rows[0]["cash_loss_usd"] == pytest.approx(rows[0]["lost_kwh"] * 0.10)

    def test_performance_metrics_defaults_to_stored_span(self, client: TestClient) -> None:
        client.post("/ingest", headers=_headers(), json=_payload())
        res = client.get("/performance-metrics", headers=_headers())
        assert res.status_code == 200, res.text
        plant = res.json()["plant"]
        assert plant["actual_kwh"] == pytest.approx(40.0)
        assert res.json()["daily"]

    def test_performance_metrics_explicit_range(self, client: TestClient) -> None:
        client.post("/ingest", headers=_headers(), json=_payload())
        start = datetime(2026, 6, 15, 8, 0, tzinfo=timezone.utc)
        end = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
        res = client.get(
            "/performance-metrics",
            headers=_headers(),
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        assert res.status_code == 200, res.text
        plant = res.json()["plant"]
        assert plant["actual_kwh"] == pytest.approx(40.0)
        assert plant["open_alerts"] == 1
        assert plant["cash_loss_usd"] > 0

    def test_metrics_range_too_large_is_400(self, client: TestClient) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        end = datetime(2026, 3, 15, tzinfo=timezone.utc)
        res = client.get(
            "/performance-metrics",
            headers=_headers(),
            params={"start": start.isoformat(), "end": end.isoformat()},
        )
        assert res.status_code == 400
        assert res.json()["code"] == "bad_range"

    def test_invalid_alert_status_is_400(self, client: TestClient) -> None:
        res = client.get("/alerts", headers=_headers(), params={"status": "nope"})
        assert res.status_code == 400

    def test_out_of_range_irradiance_is_422(self, client: TestClient) -> None:
        payload = _payload(n=1)
        payload["readings"][0]["irradiance_wm2"] = 5000
        res = client.post("/ingest", headers=_headers(), json=payload)
        assert res.status_code == 422

    def test_db_failure_is_503(self, client: TestClient) -> None:
        def boom(*_a, **_k):
            raise RuntimeError("disk full")

        client.app.state.telemetry_repo.upsert_many = boom  # type: ignore[method-assign]
        res = client.post("/ingest", headers=_headers(), json=_payload(n=1))
        assert res.status_code == 503
        assert res.json()["code"] == "db_unavailable"
