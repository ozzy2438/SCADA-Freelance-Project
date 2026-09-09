"""Training leakage and physics-baseline gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from ges_intel.application.train import train_expected_model
from ges_intel.domain.errors import ModelQualityError
from ges_intel.infrastructure.generator import generate_readings
from ges_intel.infrastructure.ml_model import RandomForestExpectedModel


class TestTrain:
    def test_training_frame_contains_only_clean_rows_and_beats_physics(self, tmp_path: Path) -> None:
        readings = generate_readings(seed=42, n_days=8)
        assert any(r.is_anomaly for r in readings)
        model_path = tmp_path / "model.joblib"
        meta_path = tmp_path / "meta.json"
        meta = train_expected_model(readings, model_path, meta_path)
        assert meta["n_clean_rows"] == sum(1 for r in readings if not r.is_anomaly)
        assert meta["rmse_model"] <= meta["rmse_physics"]
        assert model_path.exists()
        loaded = RandomForestExpectedModel(str(model_path), str(meta_path))
        loaded.load()
        night = loaded.predict(0.0, 20.0, 2.0, 100.0)
        assert night == 0.0
        noon = loaded.predict(900.0, 30.0, 12.0, 100.0)
        assert 0 < noon <= 100.0

    def test_no_clean_rows_raises(self, tmp_path: Path) -> None:
        readings = generate_readings(seed=42, n_days=4)
        only_anom = [r for r in readings if r.is_anomaly]
        if not only_anom:
            pytest.skip("generator produced no anomalies in this sample")
        with pytest.raises(ValueError):
            train_expected_model(only_anom, tmp_path / "m.joblib", tmp_path / "m.json")

    def test_model_quality_error_message(self) -> None:
        err = ModelQualityError(2.0, 1.0)
        assert "2.0000" in str(err)
