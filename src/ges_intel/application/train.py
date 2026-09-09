"""Train a RandomForest expected-power model on labeled-clean simulator rows only."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
from sklearn.ensemble import RandomForestRegressor

from ges_intel.domain.constants import DEFAULT_SEED, IRRADIANCE_FLOOR_WM2, PLANT_TIMEZONE
from ges_intel.domain.errors import ModelQualityError
from ges_intel.domain.models import GeneratedReading
from ges_intel.domain.physics import physics_expected_kw
from ges_intel.infrastructure.generator import local_hour
from ges_intel.logging_config import get_logger

logger = get_logger(__name__)

FEATURE_NAMES = ("irradiance_wm2", "temperature_c", "hour_of_day", "capacity_kw")


def _rmse(y_true: np.ndarray, y_pred: np.ndarray) -> float:
    return float(math.sqrt(float(np.mean((y_true - y_pred) ** 2))))


def _feature_matrix(rows: list[GeneratedReading], tz_name: str = PLANT_TIMEZONE) -> tuple[np.ndarray, np.ndarray]:
    x = np.array(
        [
            [r.irradiance_wm2, r.temperature_c, local_hour(r.ts_utc, tz_name), r.capacity_kw]
            for r in rows
        ],
        dtype=float,
    )
    y = np.array([r.actual_power_kw for r in rows], dtype=float)
    return x, y


def train_expected_model(
    readings: list[GeneratedReading],
    model_path: str | Path,
    metadata_path: str | Path,
    *,
    tz_name: str = PLANT_TIMEZONE,
    random_state: int = DEFAULT_SEED,
    n_estimators: int = 80,
    max_depth: int = 12,
) -> dict:
    """Fit RF on non-anomaly rows. Fails if the holdout RMSE does not beat physics."""
    clean = [r for r in readings if not r.is_anomaly]
    if any(r.is_anomaly for r in clean):
        raise RuntimeError("training frame contains anomalous rows")
    if not clean:
        raise ValueError("no clean rows available for training")

    days = sorted({r.ts_utc.date() for r in clean})
    if len(days) >= 3:
        split_idx = max(1, int(len(days) * 0.75))
        split_day = days[split_idx]
        train_rows = [r for r in clean if r.ts_utc.date() < split_day]
        test_rows = [r for r in clean if r.ts_utc.date() >= split_day]
        if not train_rows or not test_rows:
            train_rows, test_rows = clean[: int(len(clean) * 0.8)], clean[int(len(clean) * 0.8) :]
    else:
        train_rows, test_rows = clean[: int(len(clean) * 0.8)], clean[int(len(clean) * 0.8) :]

    x_train, y_train = _feature_matrix(train_rows, tz_name)
    model = RandomForestRegressor(
        n_estimators=n_estimators,
        max_depth=max_depth,
        random_state=random_state,
        min_samples_leaf=2,
        n_jobs=-1,
    )
    model.fit(x_train, y_train)

    eval_rows = [r for r in test_rows if r.irradiance_wm2 >= IRRADIANCE_FLOOR_WM2] or test_rows
    x_eval, y_eval = _feature_matrix(eval_rows, tz_name)
    rf_pred = model.predict(x_eval)
    phys_pred = np.array(
        [physics_expected_kw(r.capacity_kw, r.irradiance_wm2, r.temperature_c) for r in eval_rows]
    )
    rmse_rf = _rmse(y_eval, rf_pred)
    rmse_phys = _rmse(y_eval, phys_pred)
    logger.info("train_rmse_rf=%s train_rmse_physics=%s n_clean=%s", rmse_rf, rmse_phys, len(clean))
    if rmse_rf > rmse_phys:
        raise ModelQualityError(rmse_rf, rmse_phys)

    model_path = Path(model_path)
    metadata_path = Path(metadata_path)
    model_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(model, model_path)
    version = datetime.now(timezone.utc).strftime("rf-%Y%m%dT%H%M%SZ")
    metadata = {
        "version": version,
        "algorithm": "RandomForestRegressor",
        "features": list(FEATURE_NAMES),
        "random_state": random_state,
        "n_estimators": n_estimators,
        "max_depth": max_depth,
        "n_clean_rows": len(clean),
        "n_train": len(train_rows),
        "n_eval": len(eval_rows),
        "rmse_model": rmse_rf,
        "rmse_physics": rmse_phys,
        "sklearn_ok": True,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    logger.info("model_saved path=%s version=%s", model_path, version)
    return metadata
