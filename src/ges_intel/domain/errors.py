"""Domain and application exceptions."""

from __future__ import annotations


class GesIntelError(Exception):
    """Base error for the intelligence engine."""


class UnknownInverterError(GesIntelError):
    def __init__(self, inverter_ids: list[str]):
        self.inverter_ids = inverter_ids
        super().__init__(f"Unknown inverter_id(s): {', '.join(inverter_ids)}")


class ModelNotLoadedError(GesIntelError):
    def __init__(self, path: str = ""):
        self.path = path
        super().__init__(f"Expected-power model is not loaded ({path})")


class ModelQualityError(GesIntelError):
    def __init__(self, rmse_model: float, rmse_physics: float):
        self.rmse_model = rmse_model
        self.rmse_physics = rmse_physics
        super().__init__(
            f"RandomForest RMSE {rmse_model:.4f} did not beat physics baseline {rmse_physics:.4f}"
        )


class PersistenceError(GesIntelError):
    def __init__(self, store: str, message: str = ""):
        self.store = store
        super().__init__(message or f"{store} persistence failure")


class MetricsRangeError(GesIntelError):
    def __init__(self, message: str):
        super().__init__(message)
