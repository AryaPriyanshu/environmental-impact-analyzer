import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from src.modeling import load_model_artifact, predict_model_diagnostics
from src.train_model import _ledger_score, generate_training_data
from src.train_model import FEATURES
from src.utils import CATEGORY_BASELINES, feature_defaults


ROOT = Path(__file__).resolve().parents[1]


def test_ledger_reference_is_deterministic_and_bounded():
    scenarios = generate_training_data(n_samples=1_000, random_state=17).head(200)
    first = _ledger_score(scenarios)
    second = _ledger_score(scenarios.copy())
    assert (first == second).all()
    assert ((0 <= first) & (first <= 100)).all()


def test_model_card_discloses_baseline_and_validation_scope():
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text())
    assert metrics["ledger_baseline"]["mae"] > 0
    assert metrics["validation"]["validated_for_real_lca"] is False
    assert "synthetic" in metrics["validation"]["scope"].casefold()
    assert set(metrics["architecture"]["specialist_categories"]) == set(CATEGORY_BASELINES)


def test_out_of_domain_inputs_widen_neural_error():
    model = joblib.load(ROOT / "models" / "gadget_impact_pipeline.joblib")
    regular = {
        "category": "Laptop",
        "replaceable_battery": False,
        **feature_defaults("Laptop"),
        "daily_hours": 6.0,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "transport_km": 7_000.0,
        "grid_kg_co2_per_kwh": 0.42,
    }
    extreme = {**regular, "manufacturing_kg": 5_000.0, "active_power_w": 5_000.0, "weight_kg": 500.0}
    frame = pd.DataFrame([{key: regular.get(key) for key in FEATURES}, {key: extreme.get(key) for key in FEATURES}])
    _, errors = model.predict_with_uncertainty(frame)
    domain = model.applicability(frame)
    assert domain[0]["status"] in {"inside", "edge"}
    assert domain[1]["status"] == "outside"
    assert errors[1] > errors[0]


def test_model_artifact_and_prediction_failures_have_a_safe_fallback(tmp_path):
    missing, missing_status = load_model_artifact(tmp_path / "missing.joblib")
    assert missing is None and missing_status == "missing"

    invalid_path = tmp_path / "invalid.joblib"
    invalid_path.write_bytes(b"not a joblib artifact")
    invalid, invalid_status = load_model_artifact(invalid_path)
    assert invalid is None and invalid_status == "invalid"

    wrong_shape_path = tmp_path / "wrong-shape.joblib"
    joblib.dump({"predict": "not callable"}, wrong_shape_path)
    wrong_shape, wrong_shape_status = load_model_artifact(wrong_shape_path)
    assert wrong_shape is None and wrong_shape_status == "invalid"

    class NonFiniteModel:
        def predict(self, _frame):
            return np.asarray([float("nan")])

    prediction = predict_model_diagnostics(NonFiniteModel(), pd.DataFrame([{"category": "Laptop"}]))
    assert prediction == (None, None, None, "prediction_failed")
