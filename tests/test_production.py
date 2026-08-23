from pathlib import Path

import joblib
import pandas as pd
from fastapi.testclient import TestClient

from api import app
from src.database import CatalogRepository
from src.reporting import assessment_pdf, comparison_pdf
from src.train_model import FEATURES
from src.utils import calculate_assessment, explanation, recommendations


ROOT = Path(__file__).resolve().parents[1]


def scenario():
    return {
        "name":"Test laptop","model_number":"T-1","manufacturer":"Example","category":"Laptop",
        "source_name":"Test scenario","source_url":"","manufacturing_kg":220.0,"active_power_w":45.0,
        "daily_hours":6.0,"grid_profile":"Average","grid_kg_co2_per_kwh":0.42,"lifespan_years":5.0,
        "repairability":6.0,"recyclability_pct":70.0,"recycled_content_pct":30.0,"battery_wh":60.0,
        "replaceable_battery":False,"weight_kg":1.5,"transport_km":7000.0,"observed_field_count":0,
        "observed_energy":False,"observed_repairability":False,"observed_carbon":False,"catalog_product":False,
    }


def test_category_model_predicts_with_calibrated_uncertainty():
    model=joblib.load(ROOT/"models/gadget_impact_pipeline.joblib")
    values=scenario()
    predictions,errors=model.predict_with_uncertainty(pd.DataFrame([{key:values.get(key) for key in FEATURES}]))
    assert 0 <= predictions[0] <= 100
    assert 0 < errors[0] < 20
    assert "Laptop" in model.category_models
    assert model.snapshot_id


def test_sqlite_search_and_metadata():
    repository=CatalogRepository(ROOT/"data/catalog.db")
    matches=repository.search("iPhone",limit=10,primary_only=False)
    assert matches
    assert any("iphone" in (item["name"]+item["model_number"]).casefold() for item in matches)
    assert repository.metadata()["record_count"] >= 10_000


def test_api_health_search_and_assessment():
    client=TestClient(app)
    health=client.get("/health")
    assert health.status_code == 200
    assert health.json()["records"] >= 10_000
    search=client.get("/v1/gadgets",params={"q":"Galaxy","limit":5,"primary_only":False})
    assert search.status_code == 200
    assert search.json()["items"]
    assessment=client.post("/v1/assess",json={"category":"Smartphone","name":"Scenario phone","grid_kg_co2_per_kwh":0.7})
    assert assessment.status_code == 200
    body=assessment.json()
    assert 0 <= body["assessment"]["eco_score"] <= 100
    assert body["assessment"]["score_low"] <= body["assessment"]["score_high"]
    assert body["method"]["model_p90_error"] > 0


def test_pdf_reports_are_real_documents():
    values=scenario()
    result=calculate_assessment(values)
    single=assessment_pdf(values,result,explanation(values,result),recommendations(values,result))
    comparison=comparison_pdf([{"product":"Example Test laptop","category":"Laptop","eco_score":result.eco_score,"score_low":result.score_low,"score_high":result.score_high,"lifecycle_carbon":result.lifecycle_carbon,"annual_energy":result.annual_energy,"confidence":result.confidence}])
    assert single.startswith(b"%PDF") and len(single) > 2000
    assert comparison.startswith(b"%PDF") and len(comparison) > 1500
