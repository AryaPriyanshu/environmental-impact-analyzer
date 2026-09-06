import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

import api
from src.database import CatalogRepository


ROOT = Path(__file__).resolve().parents[1]


def test_query_search_uses_sql_candidates_without_loading_the_full_catalog(monkeypatch):
    repository = CatalogRepository(ROOT / "data" / "catalog.db")

    def reject_full_scan():
        raise AssertionError("literal query should not load the full catalogue")

    monkeypatch.setattr(repository, "_catalog", reject_full_scan)
    matches = repository.search("Galaxy S25+", limit=5, primary_only=True)
    typo_matches = repository.search("iphnoe 16", limit=5, primary_only=True)

    assert [item["name"] for item in matches] == ["Galaxy S25+"]
    assert typo_matches[0]["name"] == "iPhone 16"


def test_product_lookup_uses_the_unique_index_without_loading_the_full_catalog(monkeypatch):
    repository = CatalogRepository(ROOT / "data" / "catalog.db")

    def reject_full_scan():
        raise AssertionError("product lookup should not load the full catalogue")

    monkeypatch.setattr(repository, "_catalog", reject_full_scan)
    product = repository.get("ifixit-149")
    assert product["name"] == "iPhone 16 Pro"
    assert type(product["observed_repairability"]) is bool
    assert type(product["is_primary_record"]) is bool
    assert repository.get("missing-product") is None


def test_sql_candidate_fallback_preserves_ranked_public_records(monkeypatch):
    expected = CatalogRepository(ROOT / "data" / "catalog.db").search(
        "iPhone", limit=10, primary_only=False
    )
    repository = CatalogRepository(ROOT / "data" / "catalog.db")

    def unavailable_fts(_query):
        raise sqlite3.OperationalError("fts5 unavailable")

    monkeypatch.setattr(repository, "_fts_candidates", unavailable_fts)
    actual = repository.search("iPhone", limit=10, primary_only=False)

    assert actual == expected
    assert repository._search_frame is None


def test_readiness_check_does_not_create_a_missing_database(tmp_path):
    database_path = tmp_path / "missing.db"

    ready, metadata = CatalogRepository(database_path).readiness()

    assert not ready
    assert metadata == {}
    assert not database_path.exists()


def test_liveness_stays_up_when_model_readiness_fails(monkeypatch):
    monkeypatch.setattr(api, "model", None)
    client = TestClient(api.app)

    live = client.get("/health/live")
    ready = client.get("/health/ready")
    compatibility = client.get("/health")

    assert live.status_code == 200
    assert live.json()["status"] == "ok"
    assert ready.status_code == 503
    assert ready.json()["database_ready"]
    assert not ready.json()["model_ready"]
    assert compatibility.status_code == 503
    assert compatibility.json()["status"] == "not_ready"


def test_uptime_uses_a_monotonic_clock(monkeypatch):
    monkeypatch.setattr(api, "STARTED_MONOTONIC", 100.0)
    monkeypatch.setattr(api.time, "monotonic", lambda: 112.5)
    monkeypatch.setattr(api.time, "time", lambda: 10**12)
    client = TestClient(api.app)

    assert client.get("/health/live").json()["uptime_seconds"] == 12.5
    assert "luma_uptime_seconds 12.500" in client.get("/metrics").text


def test_metrics_use_route_templates_instead_of_product_ids():
    api.REQUESTS.clear()
    client = TestClient(api.app)

    client.get("/v1/gadgets/ifixit-149")
    client.get("/v1/gadgets/a-unique-missing-product")
    metrics = client.get("/metrics").text

    assert 'path="/v1/gadgets/{product_id}",status="200"' in metrics
    assert 'path="/v1/gadgets/{product_id}",status="404"' in metrics
    assert "ifixit-149" not in metrics
    assert "a-unique-missing-product" not in metrics


def test_unhandled_failures_are_counted_without_exposing_dynamic_paths(monkeypatch):
    api.REQUESTS.clear()

    def fail_categories():
        raise RuntimeError("simulated internal failure")

    monkeypatch.setattr(api.repository, "categories", fail_categories)
    client = TestClient(api.app, raise_server_exceptions=False)
    assert client.get("/v1/categories").status_code == 500
    metrics = client.get("/metrics").text
    assert 'path="/v1/categories",status="500"' in metrics


def test_api_rejects_unbounded_or_unknown_text_fields():
    client = TestClient(api.app)
    assert client.post("/v1/assess", json={"name": "x" * 321}).status_code == 422
    assert client.post("/v1/assess", json={"unexpected": "ignored before hardening"}).status_code == 422
    assert client.get("/v1/gadgets", params={"manufacturer": "x" * 161}).status_code == 422
    assert client.get(f"/v1/gadgets/{'x' * 201}").status_code == 422


def test_assessment_degrades_to_the_ledger_when_prediction_fails(monkeypatch):
    class BrokenModel:
        features = ["category"]

        def predict(self, _frame):
            raise RuntimeError("incompatible artifact")

    monkeypatch.setattr(api, "model", BrokenModel())
    response = TestClient(api.app).post(
        "/v1/assess",
        json={"name": "Fallback phone", "category": "Smartphone"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["method"]["model_status"] == "prediction_failed"
    assert body["method"]["model_role"] == "ledger_only"
    assert body["method"]["model_prediction"] is None
    assert 0 <= body["assessment"]["eco_score"] <= 100


def test_docker_context_excludes_common_secret_files():
    exclusions = (ROOT / ".dockerignore").read_text().splitlines()

    assert ".env" in exclusions
    assert ".streamlit/secrets.toml" in exclusions
    assert "*.key" in exclusions
    assert "*.pem" in exclusions
    assert "*credentials*.json" in exclusions
