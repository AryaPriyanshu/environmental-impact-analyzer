"""Serializable category-aware prediction wrapper with calibrated uncertainty."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd


def load_model_artifact(path: Path) -> tuple[Any | None, str]:
    """Load a trusted local model artifact without making the app unavailable.

    Joblib artifacts are Python-specific and can become unreadable after a
    dependency or class-layout change.  The deterministic lifecycle ledger is
    the production scoring path, so a missing or incompatible shadow model
    should be visible as degraded diagnostics rather than crash the product.
    """
    path = Path(path)
    if not path.is_file():
        return None, "missing"
    try:
        artifact = joblib.load(path)
    except Exception:
        return None, "invalid"
    if not callable(getattr(artifact, "predict", None)):
        return None, "invalid"
    return artifact, "ready"


def predict_model_diagnostics(
    model: Any | None,
    frame: pd.DataFrame,
) -> tuple[float | None, float | None, dict[str, Any] | None, str]:
    """Return one bounded prediction bundle or a safe ledger-only fallback."""
    if model is None:
        return None, None, None, "not_loaded"
    try:
        if hasattr(model, "predict_with_uncertainty"):
            predictions, errors = model.predict_with_uncertainty(frame)
            prediction = float(predictions[0])
            error = float(errors[0])
        else:
            prediction = float(model.predict(frame)[0])
            error = None
        if not np.isfinite(prediction) or (error is not None and (not np.isfinite(error) or error < 0)):
            raise ValueError("model returned a non-finite diagnostic")
        applicability = None
        if hasattr(model, "applicability"):
            result = model.applicability(frame)
            if result:
                candidate = result[0]
                applicability = candidate if isinstance(candidate, dict) else None
        return prediction, error, applicability, "ready"
    except Exception:
        return None, None, None, "prediction_failed"


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
        base_errors = np.asarray(
            [self.calibration.get(str(category), fallback).get("p90_absolute_error", fallback["p90_absolute_error"]) for category in frame["category"]],
            dtype=float,
        )
        applicability = self.applicability(frame)
        multipliers = np.asarray([item["uncertainty_multiplier"] for item in applicability], dtype=float)
        return predictions, base_errors * multipliers

    def applicability(self, values: Any) -> list[dict[str, Any]]:
        """Flag features outside the training distribution's broad applicability domain."""
        frame = self._frame(values)
        numeric_baseline = self.drift_baseline.get("numeric", {})
        supported_categories = set(self.category_models) | {str(value) for value in self.calibration if value != "__global__"}
        results: list[dict[str, Any]] = []
        for _, row in frame.iterrows():
            outside: list[str] = []
            for feature, bounds in numeric_baseline.items():
                if feature not in row or pd.isna(row[feature]):
                    continue
                iqr = max(float(bounds.get("q3", 0)) - float(bounds.get("q1", 0)), 1e-9)
                lower = float(bounds.get("q1", 0)) - 1.5 * iqr
                upper = float(bounds.get("q3", 0)) + 1.5 * iqr
                if float(row[feature]) < lower or float(row[feature]) > upper:
                    outside.append(feature)
            category = str(row.get("category", "Other"))
            if supported_categories and category not in supported_categories:
                outside.append("category")
            count = len(outside)
            results.append(
                {
                    "status": "inside" if count == 0 else "edge" if count <= 2 else "outside",
                    "outside_features": outside,
                    "uncertainty_multiplier": round(min(2.5, 1.0 + 0.25 * count), 2),
                }
            )
        return results

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
            "applicability_domain": "Tukey outer fences on numeric training features; unfamiliar inputs widen model error",
        }
