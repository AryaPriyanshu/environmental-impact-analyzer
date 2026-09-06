"""Focused coverage for the portable assessment scenario contract."""
from __future__ import annotations

import re
import zlib

import pytest

import src.scenarios as scenarios
from src.scenarios import (
    ASSESSMENT_INPUT_FIELDS,
    CATALOG_CONTEXT_FIELDS,
    MAX_JSON_BYTES,
    MAX_TOKEN_CHARS,
    Scenario,
    ScenarioIntegrityError,
    ScenarioTokenError,
    ScenarioValidationError,
    ScenarioVersionError,
    decode_scenario,
    encode_scenario,
    export_scenario_json,
    import_scenario_json,
)


def complete_inputs() -> dict:
    return {
        "query": "Luma Phone 1",
        "name": "Phone 1",
        "manufacturer": "Luma Labs",
        "category": "Smartphone",
        "manufacturing_kg": 61,
        "active_power_w": 4.2,
        "daily_hours": 5.5,
        "annual_energy_kwh": None,
        "grid_kg_co2_per_kwh": 0.708,
        "grid_profile": "Average",
        "lifespan_years": 4,
        "repairability": 7.1,
        "recyclability_pct": 78,
        "recycled_content_pct": 30,
        "battery_wh": 18.5,
        "replaceable_battery": False,
        "weight_kg": 0.195,
        "transport_km": 5100,
        "software_support_years": 6,
        "brand_repair_success_rate": 0.72,
        "reported_lifecycle_kg": 72,
    }


def sample_scenario(**changes) -> Scenario:
    arguments = {
        "inputs": complete_inputs(),
        "overrides": {"repairability": 8.5, "replaceable_battery": True},
        "product_id": "report-luma-phone-1",
        "region": "India",
        "theme": "dark",
        "source_snapshot": "511869fd34bfc302",
        "model_version": "3.0",
        "scoring_version": "4.0",
    }
    arguments.update(changes)
    return Scenario.create(**arguments)


def test_all_assessment_inputs_and_overrides_round_trip_in_url_safe_token():
    expected_public_inputs = {
        "query",
        "name",
        "manufacturer",
        "category",
        "manufacturing_kg",
        "active_power_w",
        "daily_hours",
        "grid_kg_co2_per_kwh",
        "lifespan_years",
        "repairability",
        "recyclability_pct",
        "recycled_content_pct",
        "battery_wh",
        "replaceable_battery",
        "weight_kg",
        "transport_km",
    }
    assert expected_public_inputs.issubset(ASSESSMENT_INPUT_FIELDS)

    scenario = sample_scenario()
    token = encode_scenario(scenario)
    restored = decode_scenario(token)

    assert re.fullmatch(r"[A-Za-z0-9_.-]+", token)
    assert len(token) < len(export_scenario_json(scenario))
    assert restored == scenario
    assert restored.to_dict() == scenario.to_dict()
    assert restored.assessment_payload()["repairability"] == 8.5
    assert restored.assessment_payload()["product_id"] == "report-luma-phone-1"


def test_catalog_payload_preserves_live_identity_and_only_applies_explicit_overrides():
    scenario = sample_scenario(
        inputs={
            **complete_inputs(),
            "name": "Spoofed saved name",
            "manufacturer": "Spoofed maker",
            "manufacturing_kg": 1,
            "repairability": 0,
        },
        overrides={"repairability": 8.5, "lifespan_years": 6},
    )

    assert CATALOG_CONTEXT_FIELDS == {
        "daily_hours",
        "grid_kg_co2_per_kwh",
        "grid_profile",
    }
    assert scenario.catalog_payload() == {
        "daily_hours": 5.5,
        "grid_kg_co2_per_kwh": 0.708,
        "grid_profile": "Average",
        "repairability": 8.5,
        "lifespan_years": 6.0,
    }
    assert scenario.fallback_payload()["name"] == "Spoofed saved name"
    assert scenario.fallback_payload()["manufacturing_kg"] == 1.0
    assert "product_id" not in scenario.fallback_payload()
    assert scenario.assessment_payload()["product_id"] == "report-luma-phone-1"


def test_json_export_import_uses_the_same_versioned_contract():
    scenario = sample_scenario()
    pretty = scenario.to_json()
    compact = scenario.to_json(pretty=False)

    assert '"schema_version": 1' in pretty
    assert "\n" not in compact
    assert Scenario.from_json(pretty) == scenario
    assert import_scenario_json(compact.encode()) == scenario


def test_url_token_rejects_payload_or_tag_tampering():
    token = sample_scenario().to_token()
    prefix, payload, tag = token.split(".")
    changed_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
    changed_tag = ("A" if tag[0] != "A" else "B") + tag[1:]

    with pytest.raises(ScenarioIntegrityError):
        Scenario.from_token(f"{prefix}.{changed_payload}.{tag}")
    with pytest.raises(ScenarioIntegrityError):
        Scenario.from_token(f"{prefix}.{payload}.{changed_tag}")


def test_optional_hmac_requires_the_same_sufficiently_long_key():
    first_key = "a deployment secret with enough entropy"
    second_key = "a different deployment secret value"
    token = sample_scenario().to_token(integrity_key=first_key)

    assert Scenario.from_token(token, integrity_key=first_key) == sample_scenario()
    with pytest.raises(ScenarioIntegrityError):
        Scenario.from_token(token)
    with pytest.raises(ScenarioIntegrityError):
        Scenario.from_token(token, integrity_key=second_key)
    with pytest.raises(ValueError, match="at least 16"):
        sample_scenario().to_token(integrity_key="too short")


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"theme": "sepia"}, "theme"),
        ({"region": ""}, "region"),
        ({"product_id": 42}, "product_id"),
        ({"source_snapshot": "x" * 129}, "source_snapshot"),
        ({"inputs": {"repairability": True}}, "number"),
        ({"inputs": {"repairability": float("nan")}}, "finite"),
        ({"inputs": {"daily_hours": 25}}, "at most 24"),
        ({"inputs": {"replaceable_battery": 1}}, "boolean"),
        ({"inputs": {"name": "   "}}, "must not be empty"),
        ({"inputs": {"name": " Laptop "}}, "leading or trailing"),
        ({"inputs": {"active_power_w": 10**10_000}}, "finite number"),
        ({"inputs": {"untrusted": "value"}}, "unsupported inputs"),
        ({"overrides": {"name": "Replacement identity"}}, "unsupported overrides"),
    ],
)
def test_creation_enforces_types_bounds_and_allow_lists(changes, message):
    with pytest.raises(ScenarioValidationError, match=message):
        sample_scenario(**changes)


def test_decoding_rejects_unknown_versions_and_noncanonical_objects():
    document = sample_scenario().to_dict()
    document["schema_version"] = 2
    with pytest.raises(ScenarioVersionError):
        Scenario.from_dict(document)

    document = sample_scenario().to_dict()
    document["unexpected"] = True
    with pytest.raises(ScenarioValidationError, match="unsupported fields"):
        Scenario.from_dict(document)

    token = sample_scenario().to_token()
    with pytest.raises(ScenarioVersionError):
        decode_scenario(token.replace("luma-s1", "luma-s2", 1))


def test_json_import_rejects_duplicate_keys_nonfinite_numbers_and_oversize_data():
    compact = sample_scenario().to_json(pretty=False)
    duplicate = compact.replace('"theme":"dark"', '"theme":"dark","theme":"light"')
    with pytest.raises(ScenarioValidationError, match="duplicate JSON field"):
        import_scenario_json(duplicate)
    with pytest.raises(ScenarioValidationError, match="invalid JSON number"):
        import_scenario_json(compact.replace('"repairability":8.5', '"repairability":NaN', 1))
    with pytest.raises(ScenarioValidationError, match="exceeds"):
        import_scenario_json(" " * (MAX_JSON_BYTES + 1))
    with pytest.raises(ScenarioValidationError, match="UTF-8"):
        import_scenario_json("\ud800")


def test_token_decoder_enforces_encoded_and_expanded_size_limits():
    with pytest.raises(ScenarioTokenError, match="at most"):
        decode_scenario("x" * (MAX_TOKEN_CHARS + 1))

    compressed_bomb = zlib.compress(b" " * (MAX_JSON_BYTES + 1), level=9)
    token = (
        f"{scenarios.TOKEN_PREFIX}.{scenarios._b64encode(compressed_bomb)}."
        f"{scenarios._b64encode(scenarios._integrity_tag(compressed_bomb, None))}"
    )
    with pytest.raises(ScenarioTokenError, match="expands beyond"):
        decode_scenario(token)


def test_scenario_copies_input_mappings_and_rejects_control_characters():
    inputs = {"name": "Original"}
    scenario = sample_scenario(inputs=inputs, overrides={})
    inputs["name"] = "Mutated"
    assert scenario.inputs["name"] == "Original"
    with pytest.raises(TypeError):
        scenario.inputs["name"] = "Cannot mutate"  # type: ignore[index]
    with pytest.raises(ScenarioValidationError, match="control characters"):
        sample_scenario(region="India\nInjected")
    with pytest.raises(ScenarioValidationError, match="control characters"):
        sample_scenario(inputs={"name": "Trusted\u202eexe", "manufacturer": "X", "category": "Laptop"})
