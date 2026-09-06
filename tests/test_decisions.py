from src.decisions import annual_use_carbon, estimated_repair_footprint, replacement_decision
from src.utils import Assessment, calculate_assessment


def _device(power: float, manufacturing: float, repairability: float = 5.0) -> dict:
    return {
        "category": "Laptop",
        "manufacturing_kg": manufacturing,
        "active_power_w": power,
        "daily_hours": 6.0,
        "grid_kg_co2_per_kwh": 0.7,
        "lifespan_years": 5.0,
        "repairability": repairability,
        "recyclability_pct": 70.0,
        "recycled_content_pct": 30.0,
        "battery_wh": 60.0,
        "replaceable_battery": False,
        "weight_kg": 1.5,
        "transport_km": 7_000.0,
        "catalog_product": False,
    }


def test_repair_footprint_is_bounded_and_improves_with_repairability():
    difficult = _device(45, 200, repairability=1)
    easy = _device(45, 200, repairability=9)
    assert 0.5 <= estimated_repair_footprint(easy) < estimated_repair_footprint(difficult)


def test_replacement_break_even_requires_real_energy_savings():
    current = _device(100, 200)
    efficient = _device(20, 180)
    result = replacement_decision(current, calculate_assessment(current), efficient, calculate_assessment(efficient))
    assert result.annual_use_savings_kg > 0
    assert result.break_even_years is not None
    assert result.replacement_upfront_kg > 180

    inefficient = _device(120, 180)
    no_payback = replacement_decision(current, calculate_assessment(current), inefficient, calculate_assessment(inefficient))
    assert no_payback.break_even_years is None
    assert "does not" in no_payback.verdict


def test_decision_helpers_fail_closed_on_nonfinite_assessment_values():
    malformed = Assessment(
        eco_score=0,
        impact_score=0,
        lifecycle_carbon=0,
        annual_energy=float("inf"),
        factors={},
        confidence=0,
        uncertainty=0,
        score_uncertainty=0,
        score_low=0,
        score_high=0,
        grid_factor=float("nan"),
    )
    assert annual_use_carbon({"grid_kg_co2_per_kwh": 10**10_000}, malformed) == 0
