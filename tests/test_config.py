"""Settings and logging smoke tests."""

from ges_intel.config import Settings, reset_settings_cache
from ges_intel.logging_config import configure_logging, get_logger


def test_settings_defaults() -> None:
    reset_settings_cache()
    settings = Settings()
    assert settings.api_key == "dev-api-key"
    assert settings.tariff_usd_per_kwh == 0.10


def test_configure_logging_idempotent() -> None:
    configure_logging("DEBUG")
    configure_logging("INFO")
    assert get_logger("ges_intel.test").name == "ges_intel.test"
