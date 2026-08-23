"""Offline data freshness, model-snapshot and distribution-drift checks."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.train_model import NUMERIC


ROOT = PROJECT_ROOT


def run_health_check(max_snapshot_age_days: int = 45) -> dict[str, Any]:
    catalog = pd.read_csv(ROOT / "data" / "official_gadgets.csv", low_memory=False)
    metadata = json.loads((ROOT / "data" / "source_metadata.json").read_text())
    metrics = json.loads((ROOT / "models" / "metrics.json").read_text())
    snapshot_date = date.fromisoformat(metadata["snapshot_date"])
    age_days = (date.today() - snapshot_date).days
    checks = {
        "catalog_minimum": {"ok": len(catalog) >= 10000, "value": len(catalog), "expected": ">= 10000"},
        "snapshot_freshness": {"ok": age_days <= max_snapshot_age_days, "value": age_days, "expected": f"<= {max_snapshot_age_days} days"},
        "model_snapshot": {"ok": metrics.get("snapshot_id") == metadata.get("snapshot_id"), "value": metrics.get("snapshot_id"), "expected": metadata.get("snapshot_id")},
        "core_categories": {"ok": {"Smartphone", "Laptop", "Tablet", "Television"}.issubset(set(catalog["category"])), "value": sorted(catalog["category"].unique().tolist())},
        "source_urls": {"ok": bool(catalog["source_url"].fillna("").str.startswith("https://").all()), "value": int(catalog["source_url"].fillna("").str.startswith("https://").sum()), "expected": len(catalog)},
    }
    drift = {}
    baseline = metrics.get("drift_baseline", {}).get("numeric", {})
    for column in NUMERIC:
        if column not in catalog or column not in baseline:
            continue
        current = pd.to_numeric(catalog[column], errors="coerce").median()
        reference = baseline[column]["median"]
        iqr = max(baseline[column]["q3"] - baseline[column]["q1"], 1e-6)
        shift = abs(float(current) - reference) / iqr
        drift[column] = {"median": round(float(current), 5), "baseline": reference, "iqr_shift": round(shift, 3), "ok": shift <= 2.0}
    checks["distribution_drift"] = {"ok": all(item["ok"] for item in drift.values()), "features": drift}
    report = {
        "status": "healthy" if all(check["ok"] for check in checks.values()) else "attention",
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "snapshot_id": metadata.get("snapshot_id"),
        "checks": checks,
    }
    (ROOT / "data" / "health_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


if __name__ == "__main__":
    result = run_health_check()
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "healthy" else 1)
