import json

import pytest

from src.projects import MAX_PROJECT_BYTES, MAX_PROJECT_ITEMS, ProjectBundle, ProjectError
from src.scenarios import Scenario


def _scenario(index: int = 1) -> Scenario:
    return Scenario.create(
        product_id=f"product-{index}",
        region="India",
        theme="dark",
        source_snapshot="snapshot-1",
        model_version="model-1",
        scoring_version="4.1",
        inputs={"name": f"Phone {index}", "manufacturer": "Example", "category": "Smartphone"},
        overrides={"daily_hours": float(index)},
    )


def test_project_round_trip_keeps_only_validated_scenarios():
    project = ProjectBundle.create([_scenario(1), _scenario(2)], "snapshot-1")

    restored = ProjectBundle.from_json(project.to_json())

    assert restored == project
    assert restored.scenarios[1].overrides["daily_hours"] == 2.0


@pytest.mark.parametrize(
    "document",
    [
        b"",
        b"[]",
        b'{"schema_version":1,"source_snapshot":"x","scenarios":[],"extra":true}',
        b'{"schema_version":1,"schema_version":1,"source_snapshot":"x","scenarios":[]}',
        b'{"schema_version":2,"source_snapshot":"x","scenarios":[{}]}',
        b'{"schema_version":1,"source_snapshot":"x","scenarios":[NaN]}',
    ],
)
def test_project_rejects_malformed_or_unsupported_documents(document):
    with pytest.raises(ProjectError):
        ProjectBundle.from_json(document)


def test_project_rejects_excessive_item_count():
    value = {
        "schema_version": 1,
        "source_snapshot": "snapshot-1",
        "scenarios": [_scenario().to_dict()] * (MAX_PROJECT_ITEMS + 1),
    }

    with pytest.raises(ProjectError, match="1–50"):
        ProjectBundle.from_json(json.dumps(value))


def test_direct_project_creation_bounds_generator_consumption():
    yielded = 0

    def scenarios():
        nonlocal yielded
        while True:
            yielded += 1
            yield _scenario(((yielded - 1) % 24) + 1)

    with pytest.raises(ProjectError, match="exceeds 50"):
        ProjectBundle.create(scenarios(), "snapshot-1")
    assert yielded == MAX_PROJECT_ITEMS + 1


def test_project_rejects_oversized_mutable_input_before_copying_and_deep_json():
    with pytest.raises(ProjectError, match="encoded bytes"):
        ProjectBundle.from_json(bytearray(MAX_PROJECT_BYTES + 1))
    with pytest.raises(ProjectError, match="deeply nested|valid JSON"):
        ProjectBundle.from_json("[" * 2000 + "]" * 2000)


def test_project_rejects_ambiguous_snapshot_text():
    with pytest.raises(ProjectError, match="leading or trailing"):
        ProjectBundle.create([_scenario()], " snapshot-1")
    with pytest.raises(ProjectError, match="control characters"):
        ProjectBundle.create([_scenario()], "snapshot\u202e1")
    with pytest.raises(ProjectError, match="UTF-8"):
        ProjectBundle.from_json("\ud800")
