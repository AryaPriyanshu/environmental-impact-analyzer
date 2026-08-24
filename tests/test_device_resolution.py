"""Regression coverage for global discovery and any-device assessment."""
from fastapi.testclient import TestClient

from api import app
from src.database import CatalogRepository
from src.device_search import infer_device_identity
from src.sync_official_data import _canonical, _computer_category
from src.utils import calculate_assessment, feature_defaults, model_blend_weight


repository = CatalogRepository()
client = TestClient(app)


def test_familiar_consumer_names_search_globally_without_category_gate():
    cases = {
        "MacBook": ("Apple", "Laptop"),
        "mac book": ("Apple", "Laptop"),
        "i phone 16": ("Apple", "Smartphone"),
        "Samsung Galaxy": ("Samsung", "Smartphone"),
        "Galaxy Tab": ("Samsung", "Tablet"),
        "Pixel Tablet": ("Google", "Tablet"),
    }
    for query, (manufacturer, category) in cases.items():
        matches = repository.search(query, limit=12, primary_only=True)
        assert matches, query
        assert str(matches[0]["manufacturer"]).casefold() == manufacturer.casefold()
        if category:
            assert matches[0]["category"] == category


def test_alias_typo_and_semantic_plus_variant_are_preserved():
    typo = repository.search("iphnoe 16", limit=3, primary_only=True)
    assert typo
    assert typo[0]["name"] == "iPhone 16"
    plus = repository.search("Galaxy S25+", limit=5, primary_only=True)
    assert plus
    assert plus[0]["name"] == "Galaxy S25+"
    assert plus[0]["observed_carbon"]
    assert _canonical("Galaxy S25+") != _canonical("Galaxy S25")
    xbox = repository.search("Xbox Series X", limit=5, primary_only=True)
    assert [item["name"] for item in xbox] == ["Xbox Series X"]


def test_reviewed_identity_records_never_become_fake_lifecycle_observations():
    matches = repository.search("PlayStation 5", limit=5, primary_only=True)
    assert matches and matches[0]["category"] == "Game console"
    assert matches[0]["source_type"] == "manufacturer_identity"
    assert not matches[0]["observed_carbon"]
    assert not matches[0]["observed_energy"]
    assert matches[0]["data_quality"] == "Identity only"
    assert matches[0]["freshness_status"] == "legacy"


def test_identity_inference_covers_major_device_families_and_unknowns():
    macbook = infer_device_identity("MacBook Pro M5")
    assert macbook["manufacturer"] == "Apple"
    assert macbook["category"] == "Laptop"
    assert infer_device_identity("Samsung Galaxy S25+")["category"] == "Smartphone"
    assert infer_device_identity("Samsung Galaxy Note 20")["category"] == "Smartphone"
    assert infer_device_identity("Google Pixel Fold")["category"] == "Smartphone"
    assert infer_device_identity("Nintendo Switch 2")["category"] == "Game console"
    assert infer_device_identity("Kindle Paperwhite")["category"] == "E-reader"
    unknown = infer_device_identity("Acme Solar Widget")
    assert unknown["manufacturer"] == "Unknown"
    assert unknown["category"] == "Other"
    assert unknown["identity_confidence"] == "Low"


def test_tablets_are_not_misclassified_as_laptops():
    assert _computer_category("Slate/Tablet") == "Tablet"
    matches = repository.search("Pixel Tablet", limit=3, primary_only=True)
    assert matches[0]["category"] == "Tablet"


def test_unlisted_devices_receive_lower_model_weight_and_wider_range():
    values = {
        "name": "Future Phone X",
        "manufacturer": "Example",
        "category": "Smartphone",
        **feature_defaults("Smartphone"),
        "daily_hours": 5.0,
        "grid_kg_co2_per_kwh": 0.42,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "replaceable_battery": False,
        "transport_km": 7000.0,
        "catalog_product": False,
    }
    result = calculate_assessment(values, ml_impact=50.0, ml_error=3.0)
    assert model_blend_weight(values) == 0.12
    assert result.confidence <= 20
    assert result.score_uncertainty >= 16
    values["category"] = "Other"
    assert model_blend_weight(values) == 0.0


def test_resolve_api_returns_candidates_or_an_explicit_fallback():
    known = client.get("/v1/resolve", params={"q": "MacBook Air"})
    assert known.status_code == 200
    assert known.json()["status"] in {"exact_product", "candidate_list"}
    assert known.json()["candidates"]
    unknown = client.get("/v1/resolve", params={"q": "Acme Solar Widget"})
    assert unknown.status_code == 200
    body = unknown.json()
    assert body["status"] == "generic_estimate_available"
    assert not body["candidates"]
    assert body["fallback"]["available"]


def test_brand_resolution_counts_the_full_catalog_and_diversifies_candidates():
    response = client.get("/v1/resolve", params={"q": "Samsung", "limit": 12})
    assert response.status_code == 200
    body = response.json()
    assert body["total_matches"] > len(body["candidates"])
    assert body["total_matches"] == sum(body["facets"]["categories"].values())
    assert {"Laptop", "Monitor", "Smartphone", "Tablet", "Television"}.issubset(body["facets"]["categories"])
    assert len({item["category"] for item in body["candidates"]}) >= 5
    metadata = repository.metadata()
    assert metadata["catalog_record_count"] == sum(item["records"] for item in repository.categories())


def test_query_only_api_assessment_is_transparently_inferred():
    response = client.post("/v1/assess", json={"query": "iPhone 99 Future"})
    assert response.status_code == 200
    body = response.json()
    assert body["product"]["manufacturer"] == "Apple"
    assert body["product"]["category"] == "Smartphone"
    assert body["resolution"]["status"] == "category_estimate"
    assert not body["resolution"]["catalog_product"]
    assert body["method"]["model_share"] == 0.12
    assert body["assessment"]["score_uncertainty"] >= 16


def test_exact_query_assessment_uses_catalog_evidence_but_identity_only_stays_weak():
    iphone = client.post("/v1/assess", json={"query": "iPhone 16"}).json()
    assert iphone["resolution"]["status"] == "catalog_match"
    assert iphone["product"]["source_name"] == "Apple Product Environmental Report"
    assert iphone["method"]["model_share"] == 0.28

    playstation = client.post("/v1/assess", json={"query": "PlayStation 5"}).json()
    assert playstation["resolution"]["status"] == "verified_identity_estimate"
    assert playstation["resolution"]["evidence_tier"] == "Verified identity + category estimate"
    assert playstation["method"]["model_share"] == 0.12
    assert playstation["assessment"]["score_uncertainty"] >= 16


def test_api_category_defaults_and_scenario_overrides_change_evidence_basis():
    router = client.post("/v1/assess", json={"query": "wifi router"}).json()
    assert router["product"]["category"] == "Router / network"
    assert router["assessment"]["annual_energy"] > 360

    baseline = client.post("/v1/assess", json={"product_id": "ifixit-149"}).json()
    overridden = client.post("/v1/assess", json={"product_id": "ifixit-149", "repairability": 0}).json()
    assert baseline["resolution"]["evidence_tier"] == "Product evidence"
    assert overridden["resolution"]["scenario_overrides"] == ["repairability"]
    assert overridden["resolution"]["evidence_tier"] == "Verified identity + category estimate"
    assert overridden["assessment"]["confidence"] < baseline["assessment"]["confidence"]


def test_product_api_keeps_observed_fields_when_no_override_is_supplied():
    product = repository.search("iPhone 16", limit=1, primary_only=True)[0]
    response = client.post("/v1/assess", json={"product_id": product["product_id"]})
    assert response.status_code == 200
    body = response.json()
    assert body["resolution"]["status"] == "catalog_match"
    assert body["resolution"]["catalog_product"]
    assert body["product"]["source_name"] == product["source_name"]
    assert body["assessment"]["lifecycle_carbon"] == product["reported_lifecycle_kg"]


def test_e_readers_use_a_distinct_low_power_category_baseline():
    kindle = repository.search("Kindle Paperwhite", limit=1, primary_only=True)[0]
    assert kindle["category"] == "E-reader"
    response = client.post("/v1/assess", json={"product_id": kindle["product_id"]})
    body = response.json()
    assert body["resolution"]["evidence_tier"] == "Verified identity + category estimate"
    assert body["assessment"]["annual_energy"] < 5


def test_unsupported_category_degrades_to_other_instead_of_rejecting_device():
    response = client.post("/v1/assess", json={"name": "Mystery device", "category": "Quantum accessory"})
    assert response.status_code == 200
    body = response.json()
    assert body["product"]["category"] == "Other"
    assert body["resolution"]["evidence_tier"] == "Generic estimate"
