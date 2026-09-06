"""Field-level evidence ledger for transparent gadget assessments."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import islice
from math import isfinite
from typing import Any


@dataclass(frozen=True)
class EvidenceEntry:
    factor: str
    value: str
    status: str
    source: str
    date: str
    improvement: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _number(value: Any) -> float | None:
    try:
        result = float(value)
        return result if isfinite(result) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "y"}
    return False


def _override_axes(value: Any) -> set[str]:
    """Normalize a small, malformed-input-tolerant set of override axes."""
    if isinstance(value, str):
        candidates = (value,)
    elif isinstance(value, (list, tuple, set, frozenset)):
        candidates = islice(value, 33)
    else:
        return set()
    result: set[str] = set()
    for item in candidates:
        if isinstance(item, str) and 0 < len(item) <= 64:
            result.add(item)
        if len(result) >= 32:
            break
    return result


def _format(value: Any, unit: str = "", decimals: int = 1) -> str:
    number = _number(value)
    if number is None:
        return "Not available"
    rendered = f"{number:,.{decimals}f}".rstrip("0").rstrip(".")
    return f"{rendered} {unit}".strip()


def evidence_ledger(values: dict[str, Any], annual_energy: float | None = None) -> list[EvidenceEntry]:
    """Describe the origin and quality of every influential assessment input."""
    overrides = _override_axes(values.get("override_axes"))
    source = str(values.get("source_name") or "Source not supplied")
    source_date = str(values.get("market_date") or values.get("retrieved_at") or "")[:10] or "Not dated"
    category_source = f"{values.get('category', 'Gadget')} baseline"

    def state(axis: str, observed_flag: str | None = None, *, scenario: bool = False) -> tuple[str, str, str]:
        if axis in overrides:
            return "Scenario override", "User scenario", "Current assessment"
        if observed_flag and _truthy(values.get(observed_flag)):
            return "Observed", source, source_date
        if scenario:
            return "Scenario", "User or regional scenario", "Current assessment"
        return "Estimated", category_source, "Current model version"

    catalog_product = _truthy(values.get("catalog_product"))
    identity_status = "Observed" if catalog_product else "Inferred" if values.get("identity_confidence") in {"High", "Moderate"} else "Scenario"
    identity_source = source if catalog_product else "Name/category resolver" if identity_status == "Inferred" else "User scenario"
    entries = [
        EvidenceEntry(
            "Product identity",
            " · ".join(str(values.get(key) or "Unknown") for key in ("manufacturer", "name", "model_number")),
            identity_status,
            identity_source,
            source_date if catalog_product else "Current assessment",
            "Confirm the exact model number and configuration." if not catalog_product else "Identity is source-backed; confirm configuration-specific specifications.",
        )
    ]

    fields = [
        ("Manufacturing carbon", "manufacturing_kg", "kg CO₂e", "materials", "observed_manufacturing", False, "Add a verified product footprint or EPD with stage boundaries."),
        ("Reported lifecycle carbon", "reported_lifecycle_kg", "kg CO₂e", "carbon", "observed_carbon", False, "Add a verified product footprint with configuration, geography and stage boundaries."),
        ("Annual energy", None, "kWh/year", "energy", "observed_energy", False, "Add a published energy label or measured annual consumption."),
        ("Daily active use", "daily_hours", "hours/day", "energy", None, True, "Use measured screen-on or active-use time."),
        ("Expected ownership", "lifespan_years", "years", "durability", "observed_durability", False, "Add durability testing and supported-lifetime evidence."),
        ("Repairability", "repairability", "/ 10", "repairability", "observed_repairability", False, "Add an official repair score, parts availability and manual."),
        ("Recyclability", "recyclability_pct", "%", "circularity", None, True, "Add a verified material and end-of-life declaration."),
        ("Recycled content", "recycled_content_pct", "%", "circularity", None, True, "Add supplier-verified recycled material content."),
        ("Battery capacity", "battery_wh", "Wh", "battery", "observed_battery", False, "Add rated capacity, cycle endurance and replacement details."),
        ("Device weight", "weight_kg", "kg", "materials", None, False, "Add a configuration-specific published device weight."),
        ("Transport distance", "transport_km", "km", "transport", None, True, "Add route, mode and assembly-to-market logistics data."),
        ("Electricity intensity", "grid_kg_co2_per_kwh", "kg CO₂e/kWh", "grid", None, True, "Use a recent local or supplier-specific electricity factor."),
    ]
    for label, key, unit, axis, observed_flag, scenario, improvement in fields:
        status, field_source, date = state(axis, observed_flag, scenario=scenario)
        raw_value = annual_energy if label == "Annual energy" and annual_energy is not None else values.get(key) if key else values.get("annual_energy_kwh")
        if label == "Annual energy" and status != "Scenario override" and values.get("annual_energy_kwh") is None and annual_energy is not None:
            status, field_source, date = "Calculated", "Active power × declared use", "Current assessment"
        entries.append(EvidenceEntry(label, _format(raw_value, unit), status, field_source, date, improvement))
    return entries
