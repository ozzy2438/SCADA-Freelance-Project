"""Application settings loaded from environment."""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from ges_intel.domain.constants import PLANT_TIMEZONE, TARIFF_USD_PER_KWH


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    database_url: str = "sqlite+pysqlite:///./data/ges.db"
    duckdb_path: str = "data/telemetry.duckdb"
    model_path: str = "artifacts/model.joblib"
    model_metadata_path: str = "artifacts/model_metadata.json"
    api_key: str = "dev-api-key"
    tariff_usd_per_kwh: float = TARIFF_USD_PER_KWH
    plant_timezone: str = PLANT_TIMEZONE
    log_level: str = "INFO"
    api_base_url: str = "http://127.0.0.1:8000"
    max_ingest_batch: int = 500
    metrics_max_span_days: int = 31


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
