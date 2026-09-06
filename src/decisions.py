"""Transparent keep, repair and replacement decision calculations."""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

from src.utils import Assessment


@dataclass(frozen=True)
class ReplacementDecision:
    keep_one_year_kg: float
    repair_footprint_kg: float
    repair_and_keep_one_year_kg: float
    replacement_upfront_kg: float
    replacement_annual_use_kg: float
    annual_use_savings_kg: float
    break_even_years: float | None
    verdict: str


def _number(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
        return number if isfinite(number) else fallback
    except (TypeError, ValueError, OverflowError):
        return fallback


def annual_use_carbon(values: dict[str, Any], assessment: Assessment) -> float:
    annual_energy = max(0.0, _number(getattr(assessment, "annual_energy", 0.0)))
    fallback_grid = max(0.0, _number(getattr(assessment, "grid_factor", 0.0)))
    grid_factor = max(0.0, _number(values.get("grid_kg_co2_per_kwh"), fallback_grid))
    carbon = annual_energy * grid_factor
    return carbon if isfinite(carbon) else 0.0


def estimated_repair_footprint(values: dict[str, Any]) -> float:
    """Estimate repair parts/service impact as a disclosed manufacturing fraction."""
    manufacturing = max(0.0, _number(values.get("manufacturing_kg")))
    repairability = min(10.0, max(0.0, _number(values.get("repairability"), 5.0)))
    # Four to eight percent covers a modest parts replacement and service trip.
    fraction = 0.08 - repairability * 0.004
    return max(0.5, manufacturing * fraction)


def replacement_decision(
    current_values: dict[str, Any],
    current_assessment: Assessment,
    replacement_values: dict[str, Any],
    replacement_assessment: Assessment,
) -> ReplacementDecision:
    """Compare one-more-year use with a candidate replacement carbon payback."""
    keep = annual_use_carbon(current_values, current_assessment)
    repair = estimated_repair_footprint(current_values)
    replacement_use = annual_use_carbon(replacement_values, replacement_assessment)
    replacement_manufacturing = max(0.0, _number(replacement_values.get("manufacturing_kg")))
    replacement_transport = max(0.0, _number(replacement_values.get("weight_kg"))) * max(
        0.0, _number(replacement_values.get("transport_km"))
    ) * 0.00012
    upfront = replacement_manufacturing + replacement_transport
    savings = keep - replacement_use
    break_even = upfront / savings if savings > 1e-9 else None
    if break_even is not None and not isfinite(break_even):
        break_even = None
    if break_even is None:
        verdict = "This replacement does not reduce annual use-phase carbon under the selected scenario, so its new-production impact does not pay back."
    elif break_even > max(1.0, _number(replacement_values.get("lifespan_years"), 4.0)):
        verdict = f"Estimated carbon payback is {break_even:.1f} years—longer than the candidate's declared ownership period. Keeping or repairing is likely preferable."
    else:
        verdict = f"Estimated carbon payback is {break_even:.1f} years if the candidate delivers the modeled energy savings for that long."
    return ReplacementDecision(
        keep_one_year_kg=round(keep, 1),
        repair_footprint_kg=round(repair, 1),
        repair_and_keep_one_year_kg=round(keep + repair, 1),
        replacement_upfront_kg=round(upfront, 1),
        replacement_annual_use_kg=round(replacement_use, 1),
        annual_use_savings_kg=round(savings, 1),
        break_even_years=round(break_even, 1) if break_even is not None else None,
        verdict=verdict,
    )
