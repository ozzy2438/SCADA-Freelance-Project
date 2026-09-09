"""CLI: seed inverters, train the expected-power model, ingest synthetic history."""

from __future__ import annotations

import argparse
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from ges_intel.application.ingest import SensorReading, ingest_readings
from ges_intel.application.train import train_expected_model
from ges_intel.config import Settings, get_settings
from ges_intel.domain.constants import DEFAULT_N_DAYS, DEFAULT_SEED
from ges_intel.infrastructure.generator import DEFAULT_FLEET, generate_readings
from ges_intel.infrastructure.duckdb_store import DuckDBTelemetryRepo
from ges_intel.infrastructure.ml_model import RandomForestExpectedModel
from ges_intel.infrastructure.postgres import (
    SqlAlchemyAlertRepo,
    SqlAlchemyInverterRepo,
    create_engine_from_url,
    create_session_factory,
    init_schema,
)
from ges_intel.logging_config import configure_logging, get_logger

logger = get_logger(__name__)


def write_compose_env(path: Path, *, force: bool = False) -> Path:
    """Write a gitignored Compose .env with generated credentials (never committed)."""
    path = Path(path)
    if path.exists() and not force:
        raise FileExistsError(f"{path} already exists (pass force=True to overwrite)")
    password = secrets.token_urlsafe(24)
    api_key = secrets.token_urlsafe(24)
    user = "ges"
    dbname = "ges"
    database_url = f"postgresql+psycopg2://{user}:{quote_plus(password)}@postgres:5432/{dbname}"
    body = "\n".join(
        [
            f"API_KEY={api_key}",
            f"POSTGRES_USER={user}",
            f"POSTGRES_PASSWORD={password}",
            f"POSTGRES_DB={dbname}",
            f"DATABASE_URL={database_url}",
            "TARIFF_USD_PER_KWH=0.10",
            "LOG_LEVEL=INFO",
            "DUCKDB_PATH=/data/telemetry.duckdb",
            "MODEL_PATH=/artifacts/model.joblib",
            "MODEL_METADATA_PATH=/artifacts/model_metadata.json",
            "API_BASE_URL=http://api:8000",
            "",
        ]
    )
    path.write_text(body, encoding="utf-8")
    logger.info("compose_env_written path=%s", path)
    return path


def seed_inverters(settings: Settings) -> SqlAlchemyInverterRepo:
    engine = create_engine_from_url(settings.database_url)
    init_schema(engine)
    factory = create_session_factory(engine)
    repo = SqlAlchemyInverterRepo(factory)
    for inverter in DEFAULT_FLEET:
        repo.upsert(inverter)
    logger.info("inverters_seeded count=%s", len(DEFAULT_FLEET))
    return repo


def run_seed(
    *,
    settings: Settings | None = None,
    n_days: int = DEFAULT_N_DAYS,
    seed: int = DEFAULT_SEED,
    if_needed: bool = False,
    batch_size: int = 250,
) -> dict:
    settings = settings or get_settings()
    configure_logging(settings.log_level)
    Path(settings.model_path).parent.mkdir(parents=True, exist_ok=True)
    Path(settings.duckdb_path).parent.mkdir(parents=True, exist_ok=True)

    engine = create_engine_from_url(settings.database_url)
    init_schema(engine)
    factory = create_session_factory(engine)
    inverter_repo = SqlAlchemyInverterRepo(factory)
    alert_repo = SqlAlchemyAlertRepo(factory)
    telemetry_repo = DuckDBTelemetryRepo(settings.duckdb_path)

    for inverter in DEFAULT_FLEET:
        inverter_repo.upsert(inverter)

    model_exists = Path(settings.model_path).exists() and Path(settings.model_metadata_path).exists()
    has_rows = telemetry_repo.count() > 0
    if if_needed and model_exists and has_rows:
        logger.info("seed_skipped_if_needed")
        return {"skipped": True}

    readings = generate_readings(seed=seed, n_days=n_days)
    train_expected_model(readings, settings.model_path, settings.model_metadata_path)
    model = RandomForestExpectedModel(settings.model_path, settings.model_metadata_path)
    model.load()

    opened = persisted = 0
    for i in range(0, len(readings), batch_size):
        chunk = readings[i : i + batch_size]
        result = ingest_readings(
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
            tariff_usd_per_kwh=settings.tariff_usd_per_kwh,
        )
        persisted += result.persisted
        opened += result.alerts_opened
    logger.info("seed_complete persisted=%s alerts_opened=%s", persisted, opened)
    return {"skipped": False, "persisted": persisted, "alerts_opened": opened, "rows": len(readings)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GES intel CLI")
    parser.add_argument("--n-days", type=int, default=DEFAULT_N_DAYS)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--if-needed", action="store_true")
    parser.add_argument("--batch-size", type=int, default=250)
    parser.add_argument(
        "command",
        nargs="?",
        default="seed",
        choices=("seed", "init-env"),
        help="seed databases or write a local Compose .env",
    )
    parser.add_argument("--env-path", default=".env")
    parser.add_argument("--force", action="store_true", help="overwrite .env for init-env")
    args = parser.parse_args(argv)
    if args.command == "init-env":
        write_compose_env(Path(args.env_path), force=args.force)
        return 0
    run_seed(n_days=args.n_days, seed=args.seed, if_needed=args.if_needed, batch_size=args.batch_size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
