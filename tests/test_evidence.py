from src.evidence import evidence_ledger


def _values() -> dict:
    return {
        "manufacturer": "Example",
        "name": "Phone",
        "model_number": "P-1",
        "category": "Smartphone",
        "source_name": "Official registry",
        "market_date": "2026-01-01",
        "catalog_product": True,
        "observed_energy": True,
        "observed_repairability": False,
        "observed_carbon": False,
        "observed_battery": True,
        "observed_durability": False,
        "annual_energy_kwh": 8.0,
        "manufacturing_kg": 62.0,
        "daily_hours": 5.0,
        "lifespan_years": 3.2,
        "repairability": 5.0,
        "recyclability_pct": 65.0,
        "recycled_content_pct": 20.0,
        "battery_wh": 15.0,
        "weight_kg": 0.19,
        "transport_km": 7_000.0,
        "grid_kg_co2_per_kwh": 0.42,
        "override_axes": [],
    }


def test_evidence_ledger_separates_observed_estimated_and_scenario_fields():
    rows = {entry.factor: entry for entry in evidence_ledger(_values(), annual_energy=8.0)}
    assert rows["Product identity"].status == "Observed"
    assert rows["Annual energy"].status == "Observed"
    assert rows["Battery capacity"].status == "Observed"
    assert rows["Repairability"].status == "Estimated"
    assert rows["Transport distance"].status == "Scenario"
    assert rows["Annual energy"].source == "Official registry"


def test_evidence_override_is_never_presented_as_observed():
    values = _values()
    values["override_axes"] = ["energy", "battery"]
    rows = {entry.factor: entry for entry in evidence_ledger(values, annual_energy=12.0)}
    assert rows["Annual energy"].status == "Scenario override"
    assert rows["Battery capacity"].status == "Scenario override"
    assert rows["Annual energy"].source == "User scenario"


def test_evidence_uses_strict_flags_and_correct_materials_axis():
    values = _values()
    values.update(
        {
            "catalog_product": "False",
            "observed_energy": "False",
            "observed_carbon": True,
            "reported_lifecycle_kg": 74,
            "observed_manufacturing": False,
            "override_axes": ["materials"],
        }
    )
    rows = {entry.factor: entry for entry in evidence_ledger(values, annual_energy=8.0)}
    assert rows["Product identity"].status == "Scenario"
    assert rows["Annual energy"].status != "Observed"
    assert rows["Manufacturing carbon"].status == "Scenario override"
    assert rows["Reported lifecycle carbon"].status == "Observed"


def test_evidence_tolerates_malformed_axes_and_huge_numbers():
    values = _values()
    values["override_axes"] = None
    values["battery_wh"] = 10**10_000
    rows = {entry.factor: entry for entry in evidence_ledger(values)}
    assert rows["Battery capacity"].value == "Not available"
