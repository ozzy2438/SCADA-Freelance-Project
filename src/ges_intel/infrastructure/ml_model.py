"""RandomForest expected-power adapter (joblib artifact)."""

from __future__ import annotations

import json
from pathlib import Path

import joblib

from ges_intel.domain.errors import ModelNotLoadedError
from ges_intel.domain.physics import clip_power_kw, physics_expected_kw
from ges_intel.logging_config import get_logger

logger = get_logger(__name__)


class PhysicsExpectedModel:
    """Transparent baseline used in tests and as a fallback comparison."""

    version = "physics-baseline-v1"

    def predict(
        self,
        irradiance_wm2: float,
        temperature_c: float,
        hour_of_day: float,
        capacity_kw: float,
    ) -> float:
        del hour_of_day
        return physics_expected_kw(capacity_kw, irradiance_wm2, temperature_c)


class RandomForestExpectedModel:
    def __init__(self, model_path: str, metadata_path: str):
        self.model_path = Path(model_path)
        self.metadata_path = Path(metadata_path)
        self._model = None
        self._meta: dict = {}

    @property
    def version(self) -> str:
        if not self._meta:
            raise ModelNotLoadedError(str(self.model_path))
        return str(self._meta.get("version", "unknown"))

    def load(self) -> None:
        if not self.model_path.exists():
            raise ModelNotLoadedError(str(self.model_path))
        self._model = joblib.load(self.model_path)
        if self.metadata_path.exists():
            self._meta = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        else:
            self._meta = {"version": "unversioned"}
        logger.info(
            "model_loaded path=%s version=%s algorithm=%s",
            self.model_path,
            self.version,
            self._meta.get("algorithm"),
        )

    def predict(
        self,
        irradiance_wm2: float,
        temperature_c: float,
        hour_of_day: float,
        capacity_kw: float,
    ) -> float:
        if self._model is None:
            raise ModelNotLoadedError(str(self.model_path))
        pred = float(
            self._model.predict([[irradiance_wm2, temperature_c, hour_of_day, capacity_kw]])[0]
        )
        return clip_power_kw(pred, capacity_kw)
