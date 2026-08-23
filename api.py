"""FastAPI service for catalogue search and explainable gadget assessments."""
from __future__ import annotations

import json
import os
import time
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import joblib
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from src.database import CatalogRepository
from src.train_model import FEATURES
from src.utils import CATEGORY_BASELINES, calculate_assessment, explanation, feature_defaults, recommendations


ROOT = Path(__file__).parent
MODEL_PATH = ROOT / "models" / "gadget_impact_pipeline.joblib"
repository = CatalogRepository(ROOT / "data" / "catalog.db")
model = joblib.load(MODEL_PATH) if MODEL_PATH.exists() else None
REQUESTS = Counter()
STARTED_AT = time.time()

app = FastAPI(
    title="Luma Gadget Impact API",
    version="3.0.0",
    description="Evidence-first gadget search and uncertainty-aware lifecycle scenarios.",
    docs_url="/docs",
    redoc_url="/redoc",
)
origins = [origin.strip() for origin in os.getenv("ALLOWED_ORIGINS", "http://localhost:8501,http://127.0.0.1:8501").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])


@app.middleware("http")
async def request_metrics(request: Request, call_next):
    started = time.perf_counter()
    response = await call_next(request)
    REQUESTS[(request.url.path, response.status_code)] += 1
    response.headers["X-Process-Time-Ms"] = f"{(time.perf_counter() - started) * 1000:.2f}"
    return response


class AssessmentInput(BaseModel):
    product_id: Optional[str] = None
    name: str = "Custom gadget"
    manufacturer: str = "Unknown"
    category: str = "Other"
    manufacturing_kg: Optional[float] = Field(None, ge=0, le=5000)
    active_power_w: Optional[float] = Field(None, ge=0, le=5000)
    daily_hours: float = Field(5.0, ge=0, le=24)
    grid_kg_co2_per_kwh: float = Field(0.42, ge=0, le=2)
    lifespan_years: Optional[float] = Field(None, ge=0.5, le=30)
    repairability: float = Field(5.0, ge=0, le=10)
    recyclability_pct: float = Field(65.0, ge=0, le=100)
    recycled_content_pct: float = Field(20.0, ge=0, le=100)
    battery_wh: Optional[float] = Field(None, ge=0, le=2000)
    replaceable_battery: bool = False
    weight_kg: Optional[float] = Field(None, ge=0, le=500)
    transport_km: float = Field(7000.0, ge=0, le=50000)


def _values(payload: AssessmentInput) -> dict:
    values = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    if payload.product_id:
        product = repository.get(payload.product_id)
        if not product:
            raise HTTPException(status_code=404, detail="Product not found")
        product.update({key: value for key, value in values.items() if value is not None and key not in {"product_id", "name", "manufacturer", "category"}})
        values = product
    if values.get("category") not in CATEGORY_BASELINES:
        raise HTTPException(status_code=422, detail=f"Unknown category. Use one of: {', '.join(CATEGORY_BASELINES)}")
    defaults = feature_defaults(values["category"])
    for key, default in defaults.items():
        if values.get(key) is None:
            values[key] = default
    values.setdefault("grid_profile", "Average")
    values.setdefault("observed_field_count", 0)
    values.setdefault("source_name", "API scenario")
    values.setdefault("source_url", "")
    values.setdefault("observed_energy", False)
    values.setdefault("observed_repairability", False)
    values.setdefault("observed_carbon", False)
    return values


@app.get("/health", tags=["Operations"])
def health():
    metadata = repository.metadata()
    return {
        "status": "ok",
        "version": app.version,
        "snapshot_id": metadata.get("snapshot_id"),
        "records": metadata.get("record_count"),
        "model_loaded": model is not None,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
    }


@app.get("/v1/categories", tags=["Catalogue"])
def categories():
    return {"items": repository.categories()}


@app.get("/v1/gadgets", tags=["Catalogue"])
def gadgets(
    q: str = Query("", max_length=120),
    category: Optional[str] = None,
    manufacturer: Optional[str] = None,
    limit: int = Query(50, ge=1, le=250),
    offset: int = Query(0, ge=0),
    primary_only: bool = True,
):
    items = repository.search(q, category, manufacturer, limit, offset, primary_only)
    return {"items": items, "limit": limit, "offset": offset, "count": len(items)}


@app.get("/v1/gadgets/{product_id}", tags=["Catalogue"])
def gadget(product_id: str):
    item = repository.get(product_id)
    if not item:
        raise HTTPException(status_code=404, detail="Product not found")
    return item


@app.post("/v1/assess", tags=["Assessment"])
def assess(payload: AssessmentInput):
    values = _values(payload)
    prediction, model_error = None, None
    if model is not None:
        input_frame = pd.DataFrame([{feature: values.get(feature) for feature in FEATURES}])
        if hasattr(model, "predict_with_uncertainty"):
            predictions, errors = model.predict_with_uncertainty(input_frame)
            prediction, model_error = float(predictions[0]), float(errors[0])
        else:
            prediction = float(model.predict(input_frame)[0])
    result = calculate_assessment(values, prediction, model_error)
    return {
        "product": {key: values.get(key) for key in ("product_id", "name", "manufacturer", "category", "source_name", "source_url")},
        "assessment": asdict(result),
        "explanation": explanation(values, result),
        "recommendations": recommendations(values, result),
        "method": {"ledger_share": 0.72, "model_share": 0.28, "model_prediction": prediction, "model_p90_error": model_error},
    }


@app.get("/v1/data-quality", tags=["Operations"])
def data_quality():
    metadata = repository.metadata()
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text())
    return {
        "snapshot": {key: metadata.get(key) for key in ("schema_version", "snapshot_id", "snapshot_date", "record_count", "unique_entities", "freshness", "data_quality")},
        "model": {key: metrics.get(key) for key in ("model_version", "trained_at", "snapshot_id", "blended", "uncertainty")},
        "limitations": [
            "Most records do not publish a complete product lifecycle assessment.",
            "Category defaults and user scenarios fill missing fields and are marked as estimates.",
            "Model validation measures recovery of the disclosed scenario target, not unknown real-world truth.",
        ],
    }


@app.get("/metrics", response_class=PlainTextResponse, include_in_schema=False)
def prometheus_metrics():
    lines = ["# HELP luma_uptime_seconds Process uptime.", "# TYPE luma_uptime_seconds gauge", f"luma_uptime_seconds {time.time() - STARTED_AT:.3f}"]
    lines.extend(["# HELP luma_http_requests_total HTTP responses by path and status.", "# TYPE luma_http_requests_total counter"])
    for (path, status), count in sorted(REQUESTS.items()):
        safe_path = path.replace('"', "")
        lines.append(f'luma_http_requests_total{{path="{safe_path}",status="{status}"}} {count}')
    return "\n".join(lines) + "\n"
