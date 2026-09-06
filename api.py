"""FastAPI service for catalogue search and explainable gadget assessments."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import FastAPI, HTTPException, Path as PathParameter, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field

from src.database import CatalogRepository
from src.device_search import infer_device_identity, normalize_search_text
from src.evidence import evidence_ledger
from src.modeling import load_model_artifact, predict_model_diagnostics
from src.train_model import FEATURES
from src.utils import CATEGORY_BASELINES, SCORING_VERSION, calculate_assessment, explanation, feature_defaults, has_product_environmental_evidence, model_blend_weight, recommendations


ROOT = Path(__file__).parent
MODEL_PATH = ROOT / "models" / "gadget_impact_pipeline.joblib"
repository = CatalogRepository(ROOT / "data" / "catalog.db")
model, MODEL_ARTIFACT_STATUS = load_model_artifact(MODEL_PATH)
try:
    MODEL_METRICS = json.loads((ROOT / "models" / "metrics.json").read_text())
except (OSError, ValueError):
    MODEL_METRICS = {}
MODEL_POINT_ESTIMATE_ENABLED = bool(MODEL_METRICS.get("validation", {}).get("validated_for_real_lca", False))
REQUESTS = Counter()
STARTED_MONOTONIC = time.monotonic()

app = FastAPI(
    title="Luma Gadget Impact API",
    version="4.1.0",
    description="Global gadget discovery and evidence-first, uncertainty-aware lifecycle scenarios.",
    docs_url="/docs",
    redoc_url="/redoc",
)
origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        route = request.scope.get("route")
        route_label = getattr(route, "path_format", None) or getattr(route, "path", None) or "__unmatched__"
        REQUESTS[(route_label, 500)] += 1
        raise
    route = request.scope.get("route")
    route_label = getattr(route, "path_format", None) or getattr(route, "path", None) or "__unmatched__"
    REQUESTS[(route_label, response.status_code)] += 1
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


class AssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    product_id: Optional[str] = Field(None, min_length=1, max_length=200)
    query: Optional[str] = Field(None, max_length=120)
    name: str = Field("Custom gadget", min_length=1, max_length=320)
    manufacturer: str = Field("Unknown", min_length=1, max_length=160)
    category: str = Field("Other", min_length=1, max_length=80)
    manufacturing_kg: Optional[float] = Field(None, ge=0, le=5000)
    active_power_w: Optional[float] = Field(None, ge=0, le=10_000)
    daily_hours: Optional[float] = Field(None, ge=0, le=24)
    grid_kg_co2_per_kwh: float = Field(0.42, ge=0, le=2)
    lifespan_years: Optional[float] = Field(None, ge=0.5, le=30)
    repairability: Optional[float] = Field(None, ge=0, le=10)
    recyclability_pct: Optional[float] = Field(None, ge=0, le=100)
    recycled_content_pct: Optional[float] = Field(None, ge=0, le=100)
    battery_wh: Optional[float] = Field(None, ge=0, le=10_000)
    replaceable_battery: Optional[bool] = None
    weight_kg: Optional[float] = Field(None, ge=0, le=500)
    transport_km: Optional[float] = Field(None, ge=0, le=50000)


def _explicit_fields(payload: AssessmentInput) -> set[str]:
    return set(payload.model_fields_set if hasattr(payload, "model_fields_set") else payload.__fields_set__)


def _apply_product_overrides(product: dict, supplied: dict, explicit_fields: set[str]) -> dict:
    allowed = explicit_fields.difference({"product_id", "query", "name", "manufacturer", "category"})
    product.update({key: supplied[key] for key in allowed if supplied.get(key) is not None})
    override_axes: set[str] = set()
    invalidated_axes: set[str] = set()
    if allowed.intersection({"active_power_w", "daily_hours"}):
        if product.get("observed_energy"):
            invalidated_axes.add("energy")
        product["annual_energy_kwh"] = None
        product["observed_energy"] = False
        override_axes.add("energy")
    if "repairability" in allowed:
        if product.get("observed_repairability"):
            invalidated_axes.add("repairability")
        product["observed_repairability"] = False
        override_axes.add("repairability")
    if allowed.intersection({"battery_wh", "replaceable_battery"}):
        if product.get("observed_battery"):
            invalidated_axes.add("battery")
        product["observed_battery"] = False
        override_axes.add("battery")
    if allowed.intersection({"lifespan_years", "software_support_years", "brand_repair_success_rate"}):
        if product.get("observed_durability") or product.get("observed_software_support"):
            invalidated_axes.add("durability")
        product["observed_durability"] = False
        product["observed_software_support"] = False
        override_axes.add("durability")
    if allowed.intersection({"manufacturing_kg", "weight_kg"}):
        override_axes.add("materials")
    if allowed.intersection({"recyclability_pct", "recycled_content_pct"}):
        override_axes.add("circularity")
    if "transport_km" in allowed:
        override_axes.add("transport")
    if "grid_kg_co2_per_kwh" in allowed:
        override_axes.add("grid")
    if "reported_lifecycle_kg" in allowed:
        if product.get("observed_carbon"):
            invalidated_axes.add("carbon")
        product["observed_carbon"] = False
        override_axes.add("carbon")

    lifecycle_drivers = {
        "manufacturing_kg",
        "active_power_w",
        "daily_hours",
        "lifespan_years",
        "weight_kg",
        "transport_km",
    }
    if allowed.intersection(lifecycle_drivers) and "reported_lifecycle_kg" not in allowed:
        if product.get("observed_carbon"):
            invalidated_axes.add("carbon")
        product["reported_lifecycle_kg"] = None
        product["observed_carbon"] = False
        override_axes.add("carbon")
    product["override_axes"] = sorted(override_axes)
    product["invalidated_observed_axes"] = sorted(invalidated_axes)
    product["catalog_product"] = True
    product["resolution_status"] = "catalog_match" if has_product_environmental_evidence(product) else "verified_identity_estimate"
    return product


def _values(payload: AssessmentInput) -> dict:
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    explicit_fields = _explicit_fields(payload)
    product = None
    if payload.product_id:
        product = repository.get(payload.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
    elif payload.query:
        resolution = repository.resolve(payload.query, limit=12)
        normalized = normalize_search_text(payload.query)
        exact = [
            item
            for item in resolution["items"]
            if normalized
            in {
                normalize_search_text(item.get("name")),
                normalize_search_text(item.get("model_number")),
                normalize_search_text(f"{item.get('manufacturer', '')} {item.get('name', '')}"),
            }
        ]
        if len(exact) == 1:
            product = exact[0]

    if product is not None:
        values = _apply_product_overrides(product, values, explicit_fields)
    else:
        if payload.query:
            inferred = infer_device_identity(payload.query)
            values["name"] = payload.query
            if payload.manufacturer == "Unknown":
                values["manufacturer"] = inferred["manufacturer"]
            if payload.category == "Other":
                values["category"] = inferred["category"]
            values["identity_confidence"] = inferred["identity_confidence"]
        values["catalog_product"] = False
        values["resolution_status"] = "category_estimate" if values.get("category") in CATEGORY_BASELINES and values.get("category") != "Other" else "generic_estimate"
    if values.get("category") not in CATEGORY_BASELINES:
        values["requested_category"] = values.get("category")
        inferred_category = infer_device_identity(f"{values.get('name', '')} {values.get('category', '')}")["category"]
        values["category"] = inferred_category if inferred_category in CATEGORY_BASELINES else "Other"
        values["resolution_status"] = "category_estimate" if values["category"] != "Other" else "generic_estimate"
    defaults = feature_defaults(values["category"])
    for key, default in defaults.items():
        if values.get(key) is None or (isinstance(values.get(key), float) and pd.isna(values.get(key))):
            values[key] = default
    scenario_defaults = {
        "daily_hours": 24.0 if values["category"] == "Router / network" else 6.0 if values["category"] in {"Laptop", "Desktop", "Monitor"} else 5.0,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "replaceable_battery": False,
        "transport_km": 7000.0,
    }
    for key, default in scenario_defaults.items():
        if values.get(key) is None or (isinstance(values.get(key), float) and pd.isna(values.get(key))):
            values[key] = default
    values.setdefault("grid_profile", "Average")
    values.setdefault("observed_field_count", 0)
    values.setdefault("source_name", "API scenario")
    values.setdefault("source_url", "")
    values.setdefault("observed_energy", False)
    values.setdefault("observed_repairability", False)
    values.setdefault("observed_carbon", False)
    values.setdefault("observed_battery", False)
    values.setdefault("observed_durability", False)
    values.setdefault("observed_software_support", False)
    return values


def _readiness_payload() -> tuple[dict, int]:
    database_ready, metadata = repository.readiness()
    model_artifact_loaded = model is not None
    model_snapshot_matches = bool(
        model_artifact_loaded
        and getattr(model, "snapshot_id", None) == metadata.get("snapshot_id")
        and MODEL_METRICS.get("snapshot_id") == metadata.get("snapshot_id")
    )
    model_ready = model_artifact_loaded and model_snapshot_matches
    ready = database_ready and model_ready
    return {
        "status": "ok" if ready else "not_ready",
        "version": app.version,
        "snapshot_id": metadata.get("snapshot_id"),
        "records": metadata.get("catalog_record_count", metadata.get("record_count")),
        "source_records": metadata.get("record_count"),
        "identity_supplements": metadata.get("identity_supplement_count", 0),
        "database_ready": database_ready,
        "model_ready": model_ready,
        "model_loaded": model_artifact_loaded,
        "model_artifact_status": MODEL_ARTIFACT_STATUS,
        "model_snapshot_matches": model_snapshot_matches,
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 1),
    }, 200 if ready else 503


@app.get("/health/live", tags=["Operations"])
def liveness():
    return {
        "status": "ok",
        "version": app.version,
        "uptime_seconds": round(time.monotonic() - STARTED_MONOTONIC, 1),
    }


@app.get("/health/ready", tags=["Operations"])
def readiness():
    payload, status_code = _readiness_payload()
    return JSONResponse(payload, status_code=status_code)


@app.get("/health", tags=["Operations"])
def health():
    """Compatibility endpoint with readiness-aware status reporting."""
    payload, status_code = _readiness_payload()
    return JSONResponse(payload, status_code=status_code)


@app.get("/v1/categories", tags=["Catalogue"])
def categories():
    return {"items": repository.categories()}


@app.get("/v1/gadgets", tags=["Catalogue"])
def gadgets(
    q: str = Query("", max_length=120),
    category: Optional[str] = Query(None, max_length=80),
    manufacturer: Optional[str] = Query(None, max_length=160),
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0, le=1_000_000),
    primary_only: bool = True,
):
    items = repository.search(q, category, manufacturer, limit, offset, primary_only)
    return {"items": items, "limit": limit, "offset": offset, "count": len(items)}


@app.get("/v1/resolve", tags=["Catalogue"])
def resolve_device(q: str = Query(..., min_length=1, max_length=120), limit: int = Query(12, ge=1, le=50)):
    """Resolve a familiar product name or return a transparent fallback."""
    resolution = repository.resolve(q, limit=limit)
    items = resolution["items"]
    normalized = normalize_search_text(q)
    exact = [
        item
        for item in items
        if normalized
        in {
            normalize_search_text(item.get("name")),
            normalize_search_text(item.get("model_number")),
            normalize_search_text(f"{item.get('manufacturer', '')} {item.get('name', '')}"),
        }
    ]
    inferred = infer_device_identity(q)
    if len(exact) == 1:
        status = "exact_product"
    elif items:
        status = "candidate_list"
    else:
        status = "category_estimate_available" if inferred["category"] != "Other" else "generic_estimate_available"
    return {
        "query": q,
        "status": status,
        "candidates": items,
        "total_matches": resolution["total"],
        "facets": resolution["facets"],
        "fallback": {
            "available": True,
            **inferred,
            "evidence_tier": "Category estimate" if inferred["category"] != "Other" else "Generic estimate",
            "message": "No model-specific lifecycle facts will be assumed; category defaults and user inputs remain explicit.",
        },
    }


@app.get("/v1/gadgets/{product_id}", tags=["Catalogue"])
def gadget(product_id: str = PathParameter(..., min_length=1, max_length=200)):
    item = repository.get(product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.post("/v1/assess", tags=["Assessment"])
def assess(payload: AssessmentInput):
    values = _values(payload)
    values["model_point_estimate_enabled"] = MODEL_POINT_ESTIMATE_ENABLED
    model_features = getattr(model, "features", FEATURES) if model is not None else FEATURES
    input_frame = pd.DataFrame([{feature: values.get(feature) for feature in model_features}])
    prediction, model_error, applicability, diagnostic_status = predict_model_diagnostics(model, input_frame)
    result = calculate_assessment(values, prediction, model_error)
    model_share = model_blend_weight(values) if prediction is not None else 0.0
    product_evidence = has_product_environmental_evidence(values)
    return {
        "product": {key: values.get(key) for key in ("product_id", "name", "manufacturer", "category", "source_name", "source_url")},
        "assessment": asdict(result),
        "explanation": explanation(values, result),
        "recommendations": recommendations(values, result),
        "evidence": [entry.to_dict() for entry in evidence_ledger(values, result.annual_energy)],
        "resolution": {
            "status": values.get("resolution_status", "catalog_match" if values.get("catalog_product") else "generic_estimate"),
            "catalog_product": bool(values.get("catalog_product")),
            "identity_confidence": values.get("identity_confidence", "Verified source" if values.get("catalog_product") else "Low"),
            "evidence_tier": "Product evidence" if product_evidence else "Verified identity + category estimate" if values.get("catalog_product") else "Category estimate" if values.get("category") != "Other" else "Generic estimate",
            "scenario_overrides": values.get("override_axes", []),
        },
        "method": {
            "scoring_version": SCORING_VERSION,
            "source_snapshot": repository.metadata().get("snapshot_id"),
            "model_version": MODEL_METRICS.get("model_version"),
            "ledger_share": round(1 - model_share, 2),
            "model_share": model_share,
            "model_prediction": prediction,
            "model_p90_error": model_error,
            "model_role": "ledger_only" if diagnostic_status != "ready" else "validated_blend" if MODEL_POINT_ESTIMATE_ENABLED else "experimental_shadow",
            "model_status": diagnostic_status,
            "applicability": applicability,
        },
    }


@app.get("/v1/data-quality", tags=["Operations"])
def data_quality():
    metadata = repository.metadata()
    metrics = MODEL_METRICS
    return {
        "snapshot": {key: metadata.get(key) for key in ("schema_version", "snapshot_id", "snapshot_date", "record_count", "unique_entities", "freshness", "data_quality")},
        "model": {key: metrics.get(key) for key in ("model_version", "trained_at", "snapshot_id", "ledger_baseline", "blended", "validation", "uncertainty")},
        "limitations": [
            "Most records do not publish a complete product lifecycle assessment.",
            "Category defaults and user scenarios fill missing fields and are marked as estimates.",
            "Model validation measures recovery of the disclosed scenario target, not unknown real-world truth.",
        ],
    }


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics():
    lines = ["# HELP luma_uptime_seconds Process uptime.", "# TYPE luma_uptime_seconds gauge", f"luma_uptime_seconds {time.monotonic() - STARTED_MONOTONIC:.3f}"]
    lines.extend(["# HELP luma_http_requests_total HTTP responses by path and status.", "# TYPE luma_http_requests_total counter"])
    for (path, status), count in sorted(REQUESTS.items()):
        safe_path = path.replace('"', "")
        lines.append(f'luma_http_requests_total{{path="{safe_path}",status="{status}"}} {count}')
    return "\n".join(lines) + "\n"
