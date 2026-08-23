"""Serializable category-aware prediction wrapper with calibrated uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class CategoryAwareImpactModel:
    """Blend a broad neural model with specialist per-category neural models."""

    global_model: Any
    category_models: dict[str, Any]
    calibration: dict[str, dict[str, float]]
    category_weights: dict[str, float]
    features: list[str]
    trained_at: str
    snapshot_id: str
    version: str = "3.0"
    training_description: str = "Physics-informed scenarios anchored to observed product distributions"
    drift_baseline: dict[str, Any] = field(default_factory=dict)

    def _frame(self, values: Any) -> pd.DataFrame:
        frame = values.copy() if isinstance(values, pd.DataFrame) else pd.DataFrame(values)
        if "grid_kg_co2_per_kwh" not in frame:
            profiles = {"Low-carbon": 0.08, "Average": 0.42, "Coal-heavy": 0.75}
            frame["grid_kg_co2_per_kwh"] = frame.get("grid_profile", pd.Series("Average", index=frame.index)).map(profiles).fillna(0.42)
        for feature in self.features:
            if feature not in frame:
                frame[feature] = False if feature == "replaceable_battery" else "Other" if feature == "category" else 0.0
        return frame[self.features]

    def predict(self, values: Any) -> np.ndarray:
        frame = self._frame(values)
        global_prediction = np.asarray(self.global_model.predict(frame), dtype=float)
        prediction = global_prediction.copy()
        for category, indices in frame.groupby("category").groups.items():
            specialist = self.category_models.get(str(category))
            if specialist is not None:
                positions = frame.index.get_indexer(indices)
                residual = np.asarray(specialist.predict(frame.loc[indices]), dtype=float)
                weight = self.category_weights.get(str(category), 0.0)
                prediction[positions] = global_prediction[positions] + weight * residual
        return np.clip(prediction, 0, 100)

    def predict_with_uncertainty(self, values: Any) -> tuple[np.ndarray, np.ndarray]:
        frame = self._frame(values)
        predictions = self.predict(frame)
        fallback = self.calibration.get("__global__", {"p90_absolute_error": 8.0})
        errors = np.asarray(
            [self.calibration.get(str(category), fallback).get("p90_absolute_error", fallback["p90_absolute_error"]) for category in frame["category"]],
            dtype=float,
        )
        return predictions, errors

    def model_card(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "trained_at": self.trained_at,
            "snapshot_id": self.snapshot_id,
            "features": self.features,
            "specialist_categories": sorted(self.category_models),
            "specialist_weights": self.category_weights,
            "training_description": self.training_description,
            "calibration": self.calibration,
        }
