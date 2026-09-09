"""DuckDB telemetry store — single-writer, unique (inverter_id, ts_utc)."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path

import duckdb

from ges_intel.domain.alerting import align_slot, to_utc
from ges_intel.domain.models import TelemetryRecord


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS telemetry (
    inverter_id VARCHAR NOT NULL,
    ts_utc TIMESTAMP NOT NULL,
    irradiance_wm2 DOUBLE NOT NULL,
    temperature_c DOUBLE NOT NULL,
    actual_power_kw DOUBLE NOT NULL,
    expected_power_kw DOUBLE NOT NULL,
    delta_kw DOUBLE NOT NULL,
    relative_deviation DOUBLE,
    eligible BOOLEAN NOT NULL,
    is_breach BOOLEAN NOT NULL,
    PRIMARY KEY (inverter_id, ts_utc)
);
"""


def _naive_utc(ts: datetime) -> datetime:
    ts = to_utc(ts).replace(tzinfo=None)
    return ts


def _aware(ts: datetime) -> datetime:
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _row_to_record(row: tuple) -> TelemetryRecord:
    (
        inverter_id,
        ts_utc,
        irradiance_wm2,
        temperature_c,
        actual_power_kw,
        expected_power_kw,
        delta_kw,
        relative_deviation,
        eligible,
        is_breach,
    ) = row
    return TelemetryRecord(
        inverter_id=inverter_id,
        ts_utc=align_slot(_aware(ts_utc)),
        irradiance_wm2=float(irradiance_wm2),
        temperature_c=float(temperature_c),
        actual_power_kw=float(actual_power_kw),
        expected_power_kw=float(expected_power_kw),
        delta_kw=float(delta_kw),
        relative_deviation=None if relative_deviation is None else float(relative_deviation),
        eligible=bool(eligible),
        is_breach=bool(is_breach),
    )


class DuckDBTelemetryRepo:
    def __init__(self, path: str):
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._lock:
            con = duckdb.connect(self.path)
            try:
                con.execute(_CREATE_SQL)
            finally:
                con.close()

    def _connect(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(self.path)

    def upsert_many(self, rows: list[TelemetryRecord]) -> int:
        if not rows:
            return 0
        payload = [
            (
                r.inverter_id,
                _naive_utc(r.ts_utc),
                r.irradiance_wm2,
                r.temperature_c,
                r.actual_power_kw,
                r.expected_power_kw,
                r.delta_kw,
                r.relative_deviation,
                r.eligible,
                r.is_breach,
            )
            for r in rows
        ]
        with self._lock:
            con = self._connect()
            try:
                con.executemany(
                    """
                    INSERT INTO telemetry (
                        inverter_id, ts_utc, irradiance_wm2, temperature_c,
                        actual_power_kw, expected_power_kw, delta_kw,
                        relative_deviation, eligible, is_breach
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT (inverter_id, ts_utc) DO UPDATE SET
                        irradiance_wm2 = excluded.irradiance_wm2,
                        temperature_c = excluded.temperature_c,
                        actual_power_kw = excluded.actual_power_kw,
                        expected_power_kw = excluded.expected_power_kw,
                        delta_kw = excluded.delta_kw,
                        relative_deviation = excluded.relative_deviation,
                        eligible = excluded.eligible,
                        is_breach = excluded.is_breach
                    """,
                    payload,
                )
            finally:
                con.close()
        return len(rows)

    def get_window(self, inverter_id: str, start: datetime, end: datetime) -> list[TelemetryRecord]:
        with self._lock:
            con = self._connect()
            try:
                result = con.execute(
                    """
                    SELECT inverter_id, ts_utc, irradiance_wm2, temperature_c,
                           actual_power_kw, expected_power_kw, delta_kw,
                           relative_deviation, eligible, is_breach
                    FROM telemetry
                    WHERE inverter_id = ? AND ts_utc >= ? AND ts_utc <= ?
                    ORDER BY ts_utc
                    """,
                    [inverter_id, _naive_utc(start), _naive_utc(end)],
                ).fetchall()
            finally:
                con.close()
        return [_row_to_record(row) for row in result]

    def iter_range(self, start: datetime, end: datetime) -> list[TelemetryRecord]:
        with self._lock:
            con = self._connect()
            try:
                result = con.execute(
                    """
                    SELECT inverter_id, ts_utc, irradiance_wm2, temperature_c,
                           actual_power_kw, expected_power_kw, delta_kw,
                           relative_deviation, eligible, is_breach
                    FROM telemetry
                    WHERE ts_utc >= ? AND ts_utc < ?
                    ORDER BY ts_utc, inverter_id
                    """,
                    [_naive_utc(start), _naive_utc(end)],
                ).fetchall()
            finally:
                con.close()
        return [_row_to_record(row) for row in result]

    def count(self) -> int:
        with self._lock:
            con = self._connect()
            try:
                value = con.execute("SELECT COUNT(*) FROM telemetry").fetchone()
            finally:
                con.close()
        return int(value[0]) if value else 0

    def time_range(self) -> tuple[datetime, datetime] | None:
        with self._lock:
            con = self._connect()
            try:
                row = con.execute("SELECT MIN(ts_utc), MAX(ts_utc) FROM telemetry").fetchone()
            finally:
                con.close()
        if not row or row[0] is None or row[1] is None:
            return None
        return _aware(row[0]), _aware(row[1])
