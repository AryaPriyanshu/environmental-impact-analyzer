"""Offline data freshness, model-snapshot and distribution-drift checks."""
from __future__ import annotations

import json
import ipaddress
import math
import os
import sys
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.train_model import NUMERIC
from src.utils import CATEGORY_BASELINES


ROOT = PROJECT_ROOT
MAX_CATALOG_BYTES = 512 * 1024 * 1024
MAX_METADATA_BYTES = 5 * 1024 * 1024
MAX_METRICS_BYTES = 10 * 1024 * 1024

REQUIRED_COLUMNS = {
    "product_id",
    "entity_key",
    "manufacturer",
    "name",
    "model_number",
    "category",
    "source_name",
    "source_url",
    "market_date",
    "is_primary_record",
}
CORE_SOURCE_MINIMUMS = {
    "ENERGY STAR Computers V9.0": 1_000,
    "ENERGY STAR Displays V8.0": 1_500,
    "ENERGY STAR Imaging Equipment V3.x": 1_500,
    "EU EPREL Smartphones & Tablets": 1_000,
    "French Repairability Index": 2_000,
}


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def _valid_https_url(value: Any) -> bool:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return False
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        return False
    hostname = (parsed.hostname or "").casefold().rstrip(".")
    if hostname == "localhost" or hostname.endswith(".localhost"):
        return False
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return False
    return bool(
        parsed.scheme == "https"
        and hostname
        and not parsed.username
        and not parsed.password
        and port in (None, 443)
    )


def _bounded_json(path: Path, limit: int, label: str, errors: list[str]) -> dict[str, Any]:
    try:
        size = path.stat().st_size
        if size > limit:
            raise ValueError(f"exceeds {limit} bytes")
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("root must be an object")
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, RecursionError, ValueError) as exc:
        errors.append(f"{label}: {exc}")
        return {}


def _catalog(path: Path, errors: list[str]) -> pd.DataFrame:
    try:
        size = path.stat().st_size
        if size > MAX_CATALOG_BYTES:
            raise ValueError(f"exceeds {MAX_CATALOG_BYTES} bytes")
        return pd.read_csv(path, low_memory=False)
    except (OSError, UnicodeError, pd.errors.ParserError, pd.errors.EmptyDataError, ValueError) as exc:
        errors.append(f"catalog: {exc}")
        return pd.DataFrame()


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _drift_feature(catalog: pd.DataFrame, baseline: Any, column: str) -> dict[str, Any]:
    if column not in catalog:
        return {"ok": False, "error": "feature missing from catalog"}
    if not isinstance(baseline, Mapping) or column not in baseline or not isinstance(baseline[column], Mapping):
        return {"ok": False, "error": "feature missing from model baseline"}
    item = baseline[column]
    reference = _finite(item.get("median"))
    q1 = _finite(item.get("q1"))
    q3 = _finite(item.get("q3"))
    current = _finite(pd.to_numeric(catalog[column], errors="coerce").median())
    if reference is None or q1 is None or q3 is None or current is None or q3 < q1:
        return {"ok": False, "error": "non-finite or invalid drift statistics"}
    iqr = max(q3 - q1, 1e-6)
    shift = abs(current - reference) / iqr
    return {
        "median": round(current, 5),
        "baseline": reference,
        "iqr_shift": round(shift, 3),
        "ok": shift <= 2.0,
    }


def _atomic_write_text(path: Path, document: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = handle.name
            handle.write(document)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    finally:
        if temporary is not None:
            Path(temporary).unlink(missing_ok=True)


def run_health_check(
    max_snapshot_age_days: int = 45,
    *,
    root: Path = ROOT,
    write_report: bool = True,
    today: date | None = None,
) -> dict[str, Any]:
    """Return a complete health report even when an input artifact is corrupt."""
    if type(max_snapshot_age_days) is not int or max_snapshot_age_days < 0:
        raise ValueError("max_snapshot_age_days must be a non-negative integer")
    root = Path(root)
    current_date = date.today() if today is None else today
    load_errors: list[str] = []
    catalog = _catalog(root / "data" / "official_gadgets.csv", load_errors)
    metadata = _bounded_json(root / "data" / "source_metadata.json", MAX_METADATA_BYTES, "metadata", load_errors)
    metrics = _bounded_json(root / "models" / "metrics.json", MAX_METRICS_BYTES, "metrics", load_errors)

    snapshot_date: date | None = None
    try:
        raw_snapshot_date = metadata.get("snapshot_date")
        if not isinstance(raw_snapshot_date, str):
            raise ValueError("snapshot_date must be an ISO date string")
        snapshot_date = date.fromisoformat(raw_snapshot_date)
    except ValueError as exc:
        load_errors.append(f"metadata: {exc}")
    age_days = (current_date - snapshot_date).days if snapshot_date is not None else None

    missing_columns = sorted(REQUIRED_COLUMNS.difference(catalog.columns))
    required_nulls = {
        column: int((catalog[column].isna() | catalog[column].fillna("").astype(str).str.strip().eq("")).sum())
        for column in ("product_id", "entity_key", "manufacturer", "name", "category", "source_name", "source_url")
        if column in catalog
    }
    source_counts = catalog["source_name"].value_counts().to_dict() if "source_name" in catalog else {}
    source_regressions = {
        source: {"records": int(source_counts.get(source, 0)), "minimum": minimum}
        for source, minimum in CORE_SOURCE_MINIMUMS.items()
        if int(source_counts.get(source, 0)) < minimum
    }
    if {"is_primary_record", "entity_key"}.issubset(catalog):
        primary_mask = catalog["is_primary_record"].map(_truthy)
        primary_counts = catalog[primary_mask].groupby("entity_key").size()
        catalog_entities = int(catalog["entity_key"].nunique())
    else:
        primary_counts = pd.Series(dtype=int)
        catalog_entities = 0

    architecture = metrics.get("architecture")
    specialist_categories = architecture.get("specialist_categories", []) if isinstance(architecture, Mapping) else []
    valid_model_categories = bool(
        isinstance(specialist_categories, list)
        and len(specialist_categories) <= 100
        and all(isinstance(item, str) and 0 < len(item) <= 120 for item in specialist_categories)
    )
    model_categories = set(specialist_categories) if valid_model_categories else set()
    expected_model_categories = set(CATEGORY_BASELINES)
    categories = set(catalog["category"].dropna().astype(str)) if "category" in catalog else set()
    source_url_valid = catalog["source_url"].map(_valid_https_url) if "source_url" in catalog else pd.Series(dtype=bool)

    metadata_record_count = metadata.get("record_count")
    metadata_unique_entities = metadata.get("unique_entities")
    metadata_snapshot_id = metadata.get("snapshot_id")
    valid_snapshot_id = isinstance(metadata_snapshot_id, str) and 0 < len(metadata_snapshot_id) <= 128
    checks: dict[str, dict[str, Any]] = {
        "input_artifacts": {"ok": not load_errors, "errors": load_errors},
        "catalog_minimum": {"ok": len(catalog) >= 10_000, "value": len(catalog), "expected": ">= 10000"},
        "snapshot_freshness": {
            "ok": age_days is not None and 0 <= age_days <= max_snapshot_age_days,
            "value": age_days,
            "expected": f"0–{max_snapshot_age_days} days (future dates fail)",
        },
        "model_snapshot": {
            "ok": valid_snapshot_id and metrics.get("snapshot_id") == metadata_snapshot_id,
            "value": metrics.get("snapshot_id"),
            "expected": metadata.get("snapshot_id"),
        },
        "metadata_counts": {
            "ok": type(metadata_record_count) is int
            and metadata_record_count == len(catalog)
            and type(metadata_unique_entities) is int
            and metadata_unique_entities == catalog_entities,
            "records": len(catalog),
            "expected_records": metadata_record_count,
            "entities": catalog_entities,
            "expected_entities": metadata_unique_entities,
        },
        "core_categories": {
            "ok": {"Smartphone", "Laptop", "Tablet", "Television"}.issubset(categories),
            "value": sorted(categories),
        },
        "source_urls": {"ok": bool(len(catalog)) and bool(source_url_valid.all()), "value": int(source_url_valid.sum()), "expected": len(catalog)},
        "required_schema": {"ok": not missing_columns, "missing": missing_columns, "expected": sorted(REQUIRED_COLUMNS)},
        "required_field_completeness": {"ok": not missing_columns and not any(required_nulls.values()), "blank_or_null": required_nulls},
        "product_id_uniqueness": {
            "ok": "product_id" in catalog and bool(catalog["product_id"].is_unique),
            "duplicates": int(catalog["product_id"].duplicated().sum()) if "product_id" in catalog else None,
        },
        "one_primary_per_entity": {
            "ok": bool(catalog_entities and len(primary_counts) == catalog_entities and primary_counts.eq(1).all()),
            "primary_entities": int(len(primary_counts)),
            "catalog_entities": catalog_entities,
        },
        "core_source_volume": {"ok": not source_regressions, "regressions": source_regressions, "current": {key: int(source_counts.get(key, 0)) for key in CORE_SOURCE_MINIMUMS}},
        "model_category_contract": {
            "ok": valid_model_categories and model_categories == expected_model_categories,
            "missing": sorted(expected_model_categories - model_categories),
            "unexpected": sorted(model_categories - expected_model_categories),
        },
    }
    drift_baseline = metrics.get("drift_baseline")
    baseline = drift_baseline.get("numeric") if isinstance(drift_baseline, Mapping) else None
    drift = {column: _drift_feature(catalog, baseline, column) for column in NUMERIC}
    checks["distribution_drift"] = {
        "ok": len(drift) == len(NUMERIC) and all(item["ok"] for item in drift.values()),
        "features": drift,
    }
    report = {
        "status": "healthy" if all(check["ok"] for check in checks.values()) else "attention",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": metadata.get("snapshot_id"),
        "checks": checks,
    }
    if write_report:
        document = json.dumps(report, indent=2, allow_nan=False) + "\n"
        _atomic_write_text(root / "data" / "health_report.json", document)
    return report


if __name__ == "__main__":
    result = run_health_check()
    print(json.dumps(result, indent=2, allow_nan=False))
    raise SystemExit(0 if result["status"] == "healthy" else 1)
