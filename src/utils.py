"""Lifecycle calculations and explainability helpers for gadget assessments.

The score is deliberately an interpretable decision aid, not a product EPD.  It
combines published observations with transparent category assumptions and keeps
uncertainty visible throughout the UI and API.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Optional


SCORING_VERSION = "4.1"


CATEGORY_BASELINES = {
    "Smartphone": {"manufacturing": 62, "power": 4, "life": 3.2, "weight": 0.19, "battery": 15},
    "Laptop": {"manufacturing": 230, "power": 42, "life": 5.0, "weight": 1.55, "battery": 58},
    "Tablet": {"manufacturing": 105, "power": 12, "life": 4.2, "weight": 0.50, "battery": 31},
    "Television": {"manufacturing": 330, "power": 95, "life": 8.0, "weight": 12.0, "battery": 0},
    "Headphones": {"manufacturing": 18, "power": 1.2, "life": 3.0, "weight": 0.28, "battery": 2.5},
    "Smartwatch": {"manufacturing": 28, "power": 0.8, "life": 3.0, "weight": 0.05, "battery": 1.3},
    "Monitor": {"manufacturing": 260, "power": 38, "life": 7.0, "weight": 6.2, "battery": 0},
    "Desktop": {"manufacturing": 420, "power": 120, "life": 7.0, "weight": 7.5, "battery": 0},
    "Printer / scanner": {"manufacturing": 120, "power": 35, "life": 6.0, "weight": 8.0, "battery": 0},
    "Speaker": {"manufacturing": 44, "power": 8, "life": 5.0, "weight": 1.7, "battery": 8},
    "Camera": {"manufacturing": 85, "power": 3, "life": 6.0, "weight": 0.55, "battery": 14},
    "Game console": {"manufacturing": 210, "power": 105, "life": 6.0, "weight": 3.0, "battery": 0},
    "Router / network": {"manufacturing": 95, "power": 42, "life": 7.0, "weight": 2.4, "battery": 0},
    "Streaming device": {"manufacturing": 43, "power": 5, "life": 5.0, "weight": 0.25, "battery": 0},
    "Spatial computer": {"manufacturing": 335, "power": 18, "life": 4.0, "weight": 0.65, "battery": 36},
    "E-reader": {"manufacturing": 45, "power": 1.5, "life": 5.0, "weight": 0.25, "battery": 7},
    "Other": {"manufacturing": 100, "power": 25, "life": 4.0, "weight": 1.0, "battery": 10},
}

GRID_KG_CO2_PER_KWH = {"Low-carbon": 0.08, "Average": 0.42, "Coal-heavy": 0.75}


@dataclass(frozen=True)
class Assessment:
    eco_score: float
    impact_score: float
    lifecycle_carbon: float
    annual_energy: float
    factors: dict[str, float]
    confidence: int
    uncertainty: float
    score_uncertainty: float
    score_low: float
    score_high: float
    grid_factor: float


def _number(value, fallback: float = 0.0) -> float:
    """Return a finite float for loosely typed CSV/API values."""
    try:
        number = float(value)
        return number if isfinite(number) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def pd_is_missing(value) -> bool:
    """Small dependency-free NaN/None check for values loaded from pandas."""
    if value is None:
        return True
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        numeric = None
    if numeric is not None and not isfinite(numeric):
        return True
    try:
        return bool(value != value)
    except (TypeError, ValueError):
        return False


def feature_defaults(category: str) -> dict[str, float]:
    base = CATEGORY_BASELINES.get(category, CATEGORY_BASELINES["Other"])
    return {
        "manufacturing_kg": base["manufacturing"],
        "active_power_w": base["power"],
        "lifespan_years": base["life"],
        "weight_kg": base["weight"],
        "battery_wh": base["battery"],
    }


OBSERVED_FLAGS = (
    "observed_carbon",
    "observed_energy",
    "observed_repairability",
    "observed_battery",
    "observed_durability",
    "observed_software_support",
)


def _truthy(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or value != value:
        return False
    return str(value).strip().casefold() in {"true", "1", "yes", "y"}


def observed_axis_count(values: dict) -> int:
    """Count environmental axes still backed by source observations."""
    return sum(_truthy(values.get(flag)) for flag in OBSERVED_FLAGS)


def has_product_environmental_evidence(values: dict) -> bool:
    """Return whether a verified identity also has product environmental data."""
    return _truthy(values.get("catalog_product")) and observed_axis_count(values) > 0


def model_blend_weight(values: dict) -> float:
    """Limit neural influence when the product identity or category is weak.

    The neural model is trained on physics-informed scenarios, so an unlisted
    name must not receive the same apparent model authority as a catalog-backed
    product.  Generic ``Other`` devices use the deterministic ledger only.
    """
    model_enabled = values.get("model_point_estimate_enabled")
    if (model_enabled is not None and not _truthy(model_enabled)) or values.get("category") == "Other":
        return 0.0
    return 0.28 if has_product_environmental_evidence(values) else 0.12


def calculate_assessment(
    values: dict,
    ml_impact: Optional[float] = None,
    ml_error: Optional[float] = None,
) -> Assessment:
    """Create an explainable 0–100 score. Higher ``eco_score`` is greener.

    ``ml_impact`` is blended with the deterministic ledger, while ``ml_error``
    widens the displayed score interval.  Direct regional grid intensity takes
    precedence over the legacy three-profile control.
    """
    life = max(_number(values.get("lifespan_years"), 4.0), 0.5)
    power = max(_number(values.get("active_power_w"), 0.0), 0.0)
    daily_hours = max(_number(values.get("daily_hours"), 2.0), 0.0)
    calculated_energy = power * daily_hours * 365 / 1000
    annual_energy = _number(values.get("annual_energy_kwh"), 0.0) or calculated_energy

    fallback_grid = GRID_KG_CO2_PER_KWH.get(values.get("grid_profile"), GRID_KG_CO2_PER_KWH["Average"])
    grid_factor = max(_number(values.get("grid_kg_co2_per_kwh"), fallback_grid), 0.0)
    use_carbon = annual_energy * life * grid_factor
    weight = max(_number(values.get("weight_kg"), 0.0), 0.0)
    transport_km = max(_number(values.get("transport_km"), 0.0), 0.0)
    transport = weight * transport_km * 0.00012

    battery_wh = max(_number(values.get("battery_wh"), 0.0), 0.0)
    replaceable_battery = _truthy(values.get("replaceable_battery", False))
    battery_penalty = battery_wh / 100 * (0.65 if replaceable_battery else 1.15)
    repairability = min(10.0, max(0.0, _number(values.get("repairability"), 5.0)))
    repair_penalty = (10 - repairability) * 2.6
    recycled_content = min(100.0, max(0.0, _number(values.get("recycled_content_pct"), 15.0)))
    recyclability = min(100.0, max(0.0, _number(values.get("recyclability_pct"), 55.0)))
    waste_penalty = (100 - recycled_content) * 0.10 + (100 - recyclability) * 0.13

    # A large observed repair dataset can gently inform the longevity factor,
    # without being misrepresented as product-model-specific evidence.
    repair_success = _number(values.get("brand_repair_success_rate"), -1.0)
    repair_evidence_adjustment = 0.0 if repair_success < 0 else (0.55 - repair_success) * 18
    software_years = _number(values.get("software_support_years"), 0.0)
    software_adjustment = 0.0 if software_years <= 0 else max(-8.0, min(8.0, (4.0 - software_years) * 2.0))

    manufacturing = max(_number(values.get("manufacturing_kg"), 0.0), 0.0)
    factors = {
        "Manufacturing & materials": min(100.0, manufacturing / (life * 0.9)),
        "Energy during use": min(100.0, use_carbon / (life * 0.8)),
        "Lifespan & repairability": min(100.0, max(0.0, 85 / life + repair_penalty + repair_evidence_adjustment + software_adjustment)),
        "Circularity & e-waste": min(100.0, waste_penalty + repair_penalty * 0.35),
        "Battery": min(100.0, battery_penalty * 4 + (0 if replaceable_battery or battery_wh == 0 else 13)),
        "Transport": min(100.0, transport * 1.8),
    }
    weights = {
        "Manufacturing & materials": 0.35,
        "Energy during use": 0.22,
        "Lifespan & repairability": 0.18,
        "Circularity & e-waste": 0.13,
        "Battery": 0.08,
        "Transport": 0.04,
    }
    ledger = sum(factors[key] * weights[key] for key in factors)
    ml_weight = model_blend_weight(values) if ml_impact is not None else 0.0
    impact = (1 - ml_weight) * ledger + ml_weight * _number(ml_impact, ledger)
    impact = min(100.0, max(0.0, impact))

    modeled_lifecycle = manufacturing + use_carbon + transport
    reported_lifecycle = values.get("reported_lifecycle_kg")
    lifecycle = modeled_lifecycle if pd_is_missing(reported_lifecycle) else max(_number(reported_lifecycle), 0.0)

    observed_axes = observed_axis_count(values)
    invalidated_axes = {str(axis) for axis in values.get("invalidated_observed_axes", [])}
    observed_count = max(0, int(_number(values.get("observed_field_count"), 0)) - len(invalidated_axes))
    catalog_product = _truthy(values.get("catalog_product"))
    product_evidence = catalog_product and observed_axes > 0
    confidence_floor = 32 if product_evidence else 22 if catalog_product and values.get("category") != "Other" else 18 if values.get("category") != "Other" else 10
    confidence = min(94, confidence_floor + observed_axes * 8 + min(observed_count, 5) * 4)
    if product_evidence and values.get("source_type") in {"manufacturer_report", "regulatory_registry"}:
        confidence = min(96, confidence + 5)

    if not product_evidence:
        lifecycle_fraction = 0.55 if values.get("category") == "Other" else 0.42
    else:
        lifecycle_fraction = 0.13 if confidence >= 80 else 0.21 if confidence >= 60 else 0.32
    uncertainty = max(1.0, lifecycle * lifecycle_fraction)
    if not product_evidence:
        base_score_error = 22.0 if values.get("category") == "Other" else 16.0
    else:
        base_score_error = 4.0 if confidence >= 80 else 7.0 if confidence >= 60 else 11.0
    model_score_error = max(0.0, _number(ml_error, 0.0))
    score_uncertainty = min(25.0, (base_score_error**2 + model_score_error**2) ** 0.5)
    eco_score = 100 - impact
    score_low = max(0.0, eco_score - score_uncertainty)
    score_high = min(100.0, eco_score + score_uncertainty)

    return Assessment(
        round(eco_score, 1),
        round(impact, 1),
        round(lifecycle, 1),
        round(annual_energy, 1),
        {name: round(value, 2) for name, value in factors.items()},
        int(confidence),
        round(uncertainty, 1),
        round(score_uncertainty, 1),
        round(score_low, 1),
        round(score_high, 1),
        round(grid_factor, 4),
    )


def impact_category(eco_score: float) -> str:
    return "Leading" if eco_score >= 75 else "Good" if eco_score >= 55 else "Fair" if eco_score >= 35 else "High impact"


def recommendations(values: dict, assessment: Assessment) -> list[str]:
    tips = []
    if _number(values.get("lifespan_years"), 4) < 4:
        tips.append("Keep the device one extra year; lifetime extension usually lowers manufacturing impact per year.")
    if _number(values.get("repairability"), 5) < 6:
        tips.append("Prefer accessible parts, published repair information and longer software support.")
    if _number(values.get("recycled_content_pct"), 15) < 30:
        tips.append("Look for verified recycled materials or a certified refurbished alternative.")
    if not _truthy(values.get("replaceable_battery")) and _number(values.get("battery_wh"), 0) > 20:
        tips.append("Choose a replaceable-battery model or confirm an affordable battery service.")
    if assessment.annual_energy > 100:
        tips.append("Enable energy-saving mode and reduce standby time.")
    if _number(values.get("transport_km"), 0) > 6000:
        tips.append("Buying locally or refurbished can reduce freight and new-production demand.")
    return tips[:4] or ["Prioritize long use, repair and responsible end-of-life collection."]


def explanation(values: dict, assessment: Assessment) -> str:
    ranked = sorted(assessment.factors.items(), key=lambda item: item[1], reverse=True)
    footprint = "manufacturer-reported product footprint" if values.get("observed_carbon") else "estimated lifecycle footprint"
    caveat = "The report configuration and geography still matter" if values.get("observed_carbon") else "This is a scenario estimate—not a product EPD"
    category = str(values.get("category", "Other") or "Other").strip()
    device_noun = {
        "Headphones": "headphone device",
        "Printer / scanner": "printer or scanner",
        "Router / network": "network device",
        "Other": "gadget",
    }.get(category, category.lower())
    return (
        f"This {device_noun} earns an eco score of "
        f"{assessment.eco_score:.0f}/100, with a plausible range of {assessment.score_low:.0f}–"
        f"{assessment.score_high:.0f}. The largest modeled pressures are {ranked[0][0].lower()} "
        f"and {ranked[1][0].lower()}. Its {footprint} is {assessment.lifecycle_carbon:.0f} kg CO₂e. "
        f"{caveat}; lifecycle carbon may vary by about ±{assessment.uncertainty:.0f} kg CO₂e as "
        "usage, electricity and sourcing assumptions change."
    )
