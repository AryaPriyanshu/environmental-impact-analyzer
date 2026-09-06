from src.utils import calculate_assessment, explanation, feature_defaults, impact_category, recommendations
from pathlib import Path
import json
import math
import pandas as pd


def sample():
    return {"category":"Laptop","manufacturing_kg":220.,"active_power_w":45.,"daily_hours":6.,"grid_profile":"Average","lifespan_years":5.,"repairability":6.,"recyclability_pct":70.,"recycled_content_pct":30.,"battery_wh":60.,"replaceable_battery":False,"weight_kg":1.5,"transport_km":7000.,"catalog_product":False}


def test_score_is_bounded_and_has_all_factors():
    result=calculate_assessment(sample(),55)
    assert 0 <= result.eco_score <= 100
    assert len(result.factors) == 6
    assert result.lifecycle_carbon > 220
    assert 0 <= result.score_low <= result.eco_score <= result.score_high <= 100
    assert result.grid_factor == .42


def test_longer_life_improves_score():
    short=sample(); long=sample(); short["lifespan_years"]=2.; long["lifespan_years"]=8.
    assert calculate_assessment(long).eco_score > calculate_assessment(short).eco_score


def test_category_defaults_and_labels():
    assert feature_defaults("Smartphone")["weight_kg"] < feature_defaults("Television")["weight_kg"]
    assert impact_category(80) == "Leading"


def test_explanations_use_natural_category_nouns():
    values = sample()
    values["category"] = "Headphones"
    result = calculate_assessment(values)
    assert explanation(values, result).startswith("This headphone device earns")
    values["category"] = "Other"
    assert explanation(values, result).startswith("This gadget earns")


def test_nonfinite_loose_inputs_fail_closed_and_string_booleans_stay_false():
    values = sample()
    values.update(
        {
            "manufacturing_kg": float("inf"),
            "active_power_w": 10**10_000,
            "weight_kg": float("nan"),
            "reported_lifecycle_kg": float("inf"),
            "replaceable_battery": "False",
            "catalog_product": "False",
            "observed_energy": True,
        }
    )
    result = calculate_assessment(values)
    assert all(
        math.isfinite(value)
        for value in (
            result.eco_score,
            result.lifecycle_carbon,
            result.annual_energy,
            result.uncertainty,
        )
    )
    assert result.confidence < 32
    assert any("replaceable-battery" in tip for tip in recommendations(values, result))


def test_official_snapshot_is_large_and_traceable():
    root=Path(__file__).resolve().parents[1]
    data=pd.read_csv(root/"data/official_gadgets.csv",low_memory=False)
    metadata=json.loads((root/"data/source_metadata.json").read_text())
    assert len(data) >= 10_000
    assert {"Smartphone","Laptop","Tablet","Desktop","Monitor","Television","Printer / scanner","Router / network"}.issubset(set(data.category))
    assert data.product_id.is_unique
    assert data.source_url.str.startswith("https://").all()
    assert metadata["record_count"] == len(data)
    assert metadata["unique_entities"] <= len(data)
    assert metadata["schema_version"] == "3.0"


def test_official_measurements_are_not_marked_as_full_lca():
    root=Path(__file__).resolve().parents[1]
    data=pd.read_csv(root/"data/official_gadgets.csv",low_memory=False)
    assert data.observed_energy.sum() > 4_000
    assert data.observed_repairability.sum() > 5_000
    assert data.observed_carbon.sum() >= 35
    assert len(data[data.category.eq("Smartphone")]) > 1_000
    assert len(data[data.category.eq("Tablet")]) > 1_000
    assert (data.manufacturing_kg.notna()).all()
    assert set(data.source_name).issuperset({"ENERGY STAR Computers V9.0","French Repairability Index","EU EPREL Smartphones & Tablets"})


def test_eprel_scale_and_support_fields_are_explicit():
    root=Path(__file__).resolve().parents[1]
    data=pd.read_csv(root/"data/official_gadgets.csv",low_memory=False)
    eprel=data[data.source_name.eq("EU EPREL Smartphones & Tablets")]
    assert len(eprel) >= 2_000
    assert eprel.repairability_index_native.dropna().between(0,5).all()
    assert eprel.repairability.dropna().between(0,10).all()
    assert eprel.battery_capacity_mah.notna().sum() > 1_000
    assert eprel.software_support_years.notna().sum() > 1_000


def test_regional_grid_and_repair_profiles_are_current_and_separate():
    root=Path(__file__).resolve().parents[1]
    grid=pd.read_csv(root/"data/grid_intensity.csv")
    repairs=pd.read_csv(root/"data/repair_profiles.csv")
    assert len(grid) >= 230
    assert "India" in set(grid.region)
    assert grid.kg_co2e_per_kwh.between(0,2).all()
    assert len(repairs) >= 250
    assert repairs.profile_scope.str.contains("not model-specific").all()
