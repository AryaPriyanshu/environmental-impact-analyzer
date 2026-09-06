"""Train category-aware neural impact models on reproducible scenarios.

There is no large public dataset of complete, like-for-like gadget LCAs.  The
supervised target is therefore a disclosed lifecycle ledger, while feature
distributions are anchored to the current source snapshot where observations
exist.  This makes the model useful for interpolation—not an oracle.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.model_selection import train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.modeling import CategoryAwareImpactModel
from src.utils import CATEGORY_BASELINES


ROOT = PROJECT_ROOT
CATEGORIES = list(CATEGORY_BASELINES)
NUMERIC = [
    "manufacturing_kg",
    "active_power_w",
    "daily_hours",
    "lifespan_years",
    "repairability",
    "recyclability_pct",
    "recycled_content_pct",
    "battery_wh",
    "weight_kg",
    "transport_km",
    "grid_kg_co2_per_kwh",
]
CATEGORICAL = ["category", "replaceable_battery"]
FEATURES = [*CATEGORICAL, *NUMERIC]


def _numeric(frame: pd.DataFrame, column: str, fallback: float) -> pd.Series:
    if column not in frame:
        return pd.Series(fallback, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce").fillna(fallback)


def _base_scenarios(category: str, count: int, official: pd.DataFrame, rng: np.random.Generator, seed: int) -> pd.DataFrame:
    base = CATEGORY_BASELINES[category]
    subset = official[official["category"].eq(category)] if not official.empty and "category" in official else pd.DataFrame()
    if len(subset):
        scenarios = subset.sample(count, replace=True, random_state=seed).reset_index(drop=True)
    else:
        scenarios = pd.DataFrame(index=range(count))
    scenarios["category"] = category
    scenarios["replaceable_battery"] = scenarios.get("replaceable_battery", pd.Series(False, index=scenarios.index)).fillna(False).astype(bool)
    defaults = {
        "manufacturing_kg": base["manufacturing"],
        "active_power_w": base["power"],
        "daily_hours": 24 if category == "Router / network" else 5,
        "lifespan_years": base["life"],
        "repairability": 5,
        "recyclability_pct": 65,
        "recycled_content_pct": 20,
        "battery_wh": base.get("battery", 0),
        "weight_kg": base["weight"],
        "transport_km": 7000,
    }
    for column, fallback in defaults.items():
        scenarios[column] = _numeric(scenarios, column, fallback)

    # Controlled perturbations create plausible use and supply-chain scenarios
    # around the observed product distributions rather than fake observations.
    scenarios["manufacturing_kg"] *= rng.lognormal(0, 0.24, count)
    scenarios["active_power_w"] = np.clip(scenarios["active_power_w"] * rng.lognormal(0, 0.28, count), 0.05, 2200)
    scenarios["daily_hours"] = np.clip(scenarios["daily_hours"] * rng.uniform(0.45, 1.6, count), 0.15, 24)
    scenarios["lifespan_years"] = np.clip(scenarios["lifespan_years"] * rng.uniform(0.55, 1.65, count), 0.75, 18)
    scenarios["repairability"] = np.clip(scenarios["repairability"] + rng.normal(0, 1.6, count), 0, 10)
    scenarios["recyclability_pct"] = np.clip(scenarios["recyclability_pct"] + rng.normal(0, 17, count), 5, 98)
    scenarios["recycled_content_pct"] = np.clip(scenarios["recycled_content_pct"] + rng.normal(0, 16, count), 0, 92)
    scenarios["battery_wh"] = np.clip(scenarios["battery_wh"] * rng.lognormal(0, 0.35, count), 0, 500)
    scenarios["weight_kg"] = np.clip(scenarios["weight_kg"] * rng.lognormal(0, 0.25, count), 0.02, 180)
    scenarios["transport_km"] = np.clip(scenarios["transport_km"] * rng.uniform(0.08, 2.1, count), 50, 20000)
    scenarios["grid_kg_co2_per_kwh"] = np.clip(rng.beta(2.0, 2.5, count) * 1.05 + 0.015, 0.015, 1.08)
    scenarios.loc[scenarios["battery_wh"].le(0.05), "replaceable_battery"] = False
    return scenarios[FEATURES]


def _ledger_score(data: pd.DataFrame) -> np.ndarray:
    """Return the deterministic reference ledger used to define scenarios."""
    life = data["lifespan_years"].clip(lower=0.5)
    use_carbon = data["active_power_w"] * data["daily_hours"] * 365 / 1000 * life * data["grid_kg_co2_per_kwh"]
    transport = data["weight_kg"] * data["transport_km"] * 0.00012
    repair_penalty = (10 - data["repairability"]) * 2.6
    factors = pd.DataFrame(
        {
            "manufacturing": np.minimum(100, data["manufacturing_kg"] / (life * 0.9)),
            "use": np.minimum(100, use_carbon / (life * 0.8)),
            "longevity": np.minimum(100, 85 / life + repair_penalty),
            "circularity": np.minimum(100, (100 - data["recycled_content_pct"]) * 0.10 + (100 - data["recyclability_pct"]) * 0.13 + repair_penalty * 0.35),
            "battery": np.minimum(100, data["battery_wh"] / 100 * np.where(data["replaceable_battery"], 0.65, 1.15) * 4 + np.where(data["battery_wh"].gt(0) & ~data["replaceable_battery"], 13, 0)),
            "transport": np.minimum(100, transport * 1.8),
        }
    )
    return np.clip(
        factors["manufacturing"] * 0.35
        + factors["use"] * 0.22
        + factors["longevity"] * 0.18
        + factors["circularity"] * 0.13
        + factors["battery"] * 0.08
        + factors["transport"] * 0.04,
        0,
        100,
    )


def _ledger_target(data: pd.DataFrame, rng: np.random.Generator) -> np.ndarray:
    target = _ledger_score(data)
    # Modest noise represents unmodelled supplier, behaviour and boundary detail.
    return np.clip(target + rng.normal(0, 2.4, len(data)), 0, 100)


def generate_training_data(n_samples: int = 16000, random_state: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(random_state)
    official_path = ROOT / "data" / "official_gadgets.csv"
    official = pd.read_csv(official_path, low_memory=False) if official_path.exists() else pd.DataFrame()
    per_category = max(500, n_samples // len(CATEGORIES))
    parts = [
        _base_scenarios(category, per_category, official, rng, random_state + index)
        for index, category in enumerate(CATEGORIES)
    ]
    data = pd.concat(parts, ignore_index=True)
    data["impact_score"] = _ledger_target(data, rng)
    data["scenario_id"] = [f"scenario-{index:06d}" for index in range(len(data))]
    return data.sample(frac=1, random_state=random_state).reset_index(drop=True)


def _pipeline(hidden_layers: tuple[int, ...], random_state: int) -> Pipeline:
    numeric = Pipeline([("imputer", SimpleImputer(strategy="median")), ("scale", StandardScaler())])
    categorical = Pipeline([("imputer", SimpleImputer(strategy="most_frequent")), ("encode", OneHotEncoder(handle_unknown="ignore"))])
    preparation = ColumnTransformer([("numeric", numeric, NUMERIC), ("categorical", categorical, CATEGORICAL)])
    neural = MLPRegressor(
        hidden_layer_sizes=hidden_layers,
        activation="relu",
        early_stopping=True,
        validation_fraction=0.15,
        n_iter_no_change=24,
        max_iter=600,
        random_state=random_state,
    )
    return Pipeline([("preprocessor", preparation), ("model", neural)])


def _scores(actual: pd.Series, predicted: np.ndarray) -> dict[str, float]:
    errors = np.abs(np.asarray(actual) - np.asarray(predicted))
    return {
        "samples": int(len(actual)),
        "mae": round(float(mean_absolute_error(actual, predicted)), 3),
        "p90_absolute_error": round(float(np.quantile(errors, 0.90)), 3),
        "r2": round(float(r2_score(actual, predicted)), 3),
    }


def _drift_baseline(data: pd.DataFrame) -> dict[str, Any]:
    numeric = {
        column: {
            "median": round(float(data[column].median()), 5),
            "q1": round(float(data[column].quantile(0.25)), 5),
            "q3": round(float(data[column].quantile(0.75)), 5),
        }
        for column in NUMERIC
    }
    return {
        "numeric": numeric,
        "category_share": {key: round(float(value), 5) for key, value in data["category"].value_counts(normalize=True).to_dict().items()},
    }


def train_model(n_samples: int = 16000, random_state: int = 42) -> dict[str, Any]:
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "models").mkdir(exist_ok=True)
    data = generate_training_data(n_samples=n_samples, random_state=random_state)
    data.to_csv(ROOT / "data" / "gadget_training_scenarios.csv", index=False)
    train, test = train_test_split(data, test_size=0.20, random_state=random_state, stratify=data["category"])

    global_model = _pipeline((128, 64, 32), random_state)
    global_model.fit(train[FEATURES], train["impact_score"])
    global_prediction = global_model.predict(test[FEATURES])
    global_metrics = _scores(test["impact_score"], global_prediction)
    ledger_metrics = _scores(test["impact_score"], _ledger_score(test))

    category_models, category_metrics, category_weights, calibration = {}, {}, {}, {"__global__": global_metrics}
    blended = global_prediction.copy()
    for index, category in enumerate(CATEGORIES):
        category_train = train[train["category"].eq(category)]
        category_test = test[test["category"].eq(category)]
        if len(category_train) < 300 or len(category_test) < 60:
            continue
        specialist_fit, specialist_validation = train_test_split(category_train, test_size=0.20, random_state=random_state + index)
        provisional = _pipeline((64, 32), random_state + index + 1)
        fit_residual = specialist_fit["impact_score"] - global_model.predict(specialist_fit[FEATURES])
        provisional.fit(specialist_fit[FEATURES], fit_residual)
        validation_global = global_model.predict(specialist_validation[FEATURES])
        validation_residual = provisional.predict(specialist_validation[FEATURES])
        candidate_weights = [0.0, 0.25, 0.5, 0.75, 1.0]
        weight = min(
            candidate_weights,
            key=lambda candidate: mean_absolute_error(
                specialist_validation["impact_score"], validation_global + candidate * validation_residual
            ),
        )
        specialist = _pipeline((64, 32), random_state + index + 101)
        train_residual = category_train["impact_score"] - global_model.predict(category_train[FEATURES])
        specialist.fit(category_train[FEATURES], train_residual)
        specialist_residual = specialist.predict(category_test[FEATURES])
        positions = test.index.get_indexer(category_test.index)
        category_blended = global_prediction[positions] + weight * specialist_residual
        blended[positions] = category_blended
        metrics = _scores(category_test["impact_score"], category_blended)
        metrics["global_mae"] = round(float(mean_absolute_error(category_test["impact_score"], global_prediction[positions])), 3)
        metrics["residual_weight"] = weight
        category_models[category] = specialist
        category_metrics[category] = metrics
        category_weights[category] = weight
        calibration[category] = metrics

    blended_metrics = _scores(test["impact_score"], blended)
    neural_mae_improvement = round(float(ledger_metrics["mae"] - blended_metrics["mae"]), 3)
    metadata_path = ROOT / "data" / "source_metadata.json"
    source_metadata = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
    trained_at = datetime.now(timezone.utc).isoformat()
    wrapper = CategoryAwareImpactModel(
        global_model=global_model,
        category_models=category_models,
        calibration=calibration,
        category_weights=category_weights,
        features=FEATURES,
        trained_at=trained_at,
        snapshot_id=source_metadata.get("snapshot_id", "unknown"),
        drift_baseline=_drift_baseline(data),
    )
    joblib.dump(wrapper, ROOT / "models" / "gadget_impact_pipeline.joblib")

    metrics = {
        "model_version": wrapper.version,
        "trained_at": trained_at,
        "snapshot_id": wrapper.snapshot_id,
        "samples": len(data),
        "test_samples": len(test),
        "global": global_metrics,
        "blended": blended_metrics,
        "ledger_baseline": ledger_metrics,
        "validation": {
            "scope": "Synthetic scenario-target reconstruction; not empirical lifecycle-carbon validation",
            "split": "Random category-stratified scenario holdout",
            "validated_for_real_lca": False,
            "neural_mae_improvement_over_ledger": neural_mae_improvement,
            "deployment_status": "experimental_assist" if neural_mae_improvement <= 0 else "synthetic_target_candidate",
        },
        "categories": category_metrics,
        "random_state": random_state,
        "architecture": {
            "global_hidden_layers": [128, 64, 32],
            "specialist_hidden_layers": [64, 32],
            "specialist_categories": sorted(category_models),
            "blend": "Global prediction plus validation-selected category residual",
            "category_residual_weights": category_weights,
        },
        "features": FEATURES,
        "data": "Balanced physics-informed scenarios anchored to observed product distributions; not empirical full-LCA labels",
        "uncertainty": "Per-category 90th-percentile holdout absolute error, combined with evidence coverage in scoring",
        "drift_baseline": wrapper.drift_baseline,
    }
    (ROOT / "models" / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    print(json.dumps(metrics, indent=2))
    return metrics


if __name__ == "__main__":
    train_model()
