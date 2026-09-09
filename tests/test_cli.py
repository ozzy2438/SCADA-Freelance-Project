"""End-to-end seed + train + ingest smoke test."""

from __future__ import annotations

from pathlib import Path

import pytest

from ges_intel.cli import run_seed, write_compose_env
from ges_intel.config import Settings


def test_run_seed_trains_and_ingests(tmp_path: Path) -> None:
    settings = Settings(
        database_url=f"sqlite+pysqlite:///{tmp_path}/ges.db",
        duckdb_path=str(tmp_path / "t.duckdb"),
        model_path=str(tmp_path / "model.joblib"),
        model_metadata_path=str(tmp_path / "meta.json"),
        api_key="test-key",
    )
    result = run_seed(settings=settings, n_days=8, seed=42, batch_size=400)
    assert result["skipped"] is False
    assert result["persisted"] > 0
    again = run_seed(settings=settings, n_days=8, seed=42, if_needed=True)
    assert again["skipped"] is True


def test_init_env_writes_required_keys(tmp_path: Path) -> None:
    path = tmp_path / "compose.env"
    write_compose_env(path)
    text = path.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=" in text
    assert "DATABASE_URL=postgresql+psycopg2://" in text
    assert "API_KEY=" in text
    with pytest.raises(FileExistsError):
        write_compose_env(path)
