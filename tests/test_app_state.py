"""Streamlit state regressions for share links and theme changes."""
import json
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pytest
from streamlit.testing.v1 import AppTest

from src.scenarios import Scenario


ROOT = Path(__file__).resolve().parents[1]


def _comparison_chart_frame(app: AppTest):
    chart = app.get("arrow_vega_lite_chart")[0]
    if chart.proto.datasets:
        dataset = chart.proto.datasets[0]
        frame = pa.ipc.open_stream(dataset.data.data).read_all().to_pandas()
    else:
        spec = json.loads(chart.proto.spec)
        frame = pd.DataFrame(spec["layer"][0]["data"]["values"])
    return frame.set_index("Product")


def test_shared_theme_product_and_region_replace_stale_session_state():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.query_params["theme"] = "light"
    app.run()
    assert not app.exception
    assert app.session_state["ui_theme"] == "light"

    app.query_params["theme"] = "dark"
    app.run()
    assert not app.exception
    assert app.session_state["ui_theme"] == "dark"
    assert app.session_state["theme_control"] is True

    app.query_params["tab"] = "analyse"
    app.query_params["product"] = "report-9f4b4675623addd7"
    app.query_params["region"] = "India"
    app.run()
    assert not app.exception
    assert app.session_state["analysis_input_mode"] == "Search catalogue"
    assert app.session_state["analysis_search"] == "iPhone 16"
    assert app.session_state["analysis_region"] == "India"

    app.session_state["analysis_input_mode"] = "Any device estimate"
    app.query_params["product"] = "es-rxdj-2c88-4572858"
    app.run()
    assert not app.exception
    assert app.session_state["analysis_input_mode"] == "Search catalogue"
    assert app.session_state["analysis_search"] == "A3449"


def test_ui_marks_a_sourced_field_as_a_scenario_after_override():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=20)
    app.query_params.update({"theme": "dark", "tab": "analyse", "product": "ifixit-149", "region": "India"})
    app.run()
    assert not app.exception
    repairability = next(slider for slider in app.slider if slider.label == "Repairability")
    repairability.set_value(0).run()
    assert not app.exception
    rendered = "\n".join(markdown.value for markdown in app.markdown)
    assert "<span>Repairability</span><span class=\"estimated\">Scenario override</span>" in rendered
    assert "Verified identity · category estimate" in rendered


def test_comparison_uses_one_scenario_and_leads_with_uncertainty():
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    assert not app.exception

    region = next(selectbox for selectbox in app.selectbox if selectbox.label == "Shared electricity region")
    daily_use = next(slider for slider in app.slider if slider.label == "Shared daily active use")
    ownership = next(slider for slider in app.slider if slider.label == "Shared expected ownership")
    assert region.value == "India"
    assert daily_use.value == 5.0
    assert ownership.value == 5.0
    assert any("India 2025 grid (670 g CO₂e/kWh)" in caption.value for caption in app.caption)

    candidate_search = next(field for field in app.text_input if field.label == "Search candidates")
    candidate_search.set_value("Apple").run()
    selected = next(widget for widget in app.multiselect if widget.label == "Select 2–5 records")
    selected.set_value([selected.options[0], selected.options[2]]).run()
    assert not app.exception

    initial = _comparison_chart_frame(app)
    next(slider for slider in app.slider if slider.label == "Shared daily active use").set_value(10.0).run()
    higher_use = _comparison_chart_frame(app)
    for product in initial.index:
        assert higher_use.loc[product, "Annual energy kWh"] == pytest.approx(
            initial.loc[product, "Annual energy kWh"] * 2,
            rel=0.01,
        )

    next(slider for slider in app.slider if slider.label == "Shared expected ownership").set_value(10.0).run()
    longer_ownership = _comparison_chart_frame(app)
    assert (longer_ownership["Modeled lifecycle kg CO₂e"] > higher_use["Modeled lifecycle kg CO₂e"]).all()

    warnings = "\n".join(warning.value for warning in app.warning)
    assert "different purposes" in warnings
    assert "Inconclusive score order" in warnings
    assert set(longer_ownership["Interval reading"]) == {"Inconclusive — overlaps"}

    charts = app.get("arrow_vega_lite_chart")
    interval_spec = json.loads(charts[0].proto.spec)
    factor_spec = json.loads(charts[1].proto.spec)
    assert interval_spec["layer"][0]["encoding"]["x"]["field"] == "Likely low"
    assert interval_spec["layer"][0]["encoding"]["x2"]["field"] == "Likely high"
    factor_encoding = factor_spec["layer"][0]["encoding"]
    assert factor_encoding["x"]["field"] == "Δ vs highest point estimate"
    assert factor_encoding["x"]["stack"] is None
    factor_domain = factor_encoding["x"]["scale"]["domain"]
    assert factor_domain[0] == -factor_domain[1]
    assert factor_domain[1] >= 1


@pytest.mark.parametrize(
    "product_id",
    [
        "es-qbg3-d468-2349550",  # Long ENERGY STAR product name.
        "es-n8cx-m62r-4429185",  # Source-backed power above 5 kW.
        "es-rxdj-2c88-4513954",  # Source-backed battery above 2 kWh.
    ],
)
def test_catalog_extremes_render_without_widget_or_scenario_failures(product_id):
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params.update({"tab": "analyse", "product": product_id, "region": "India"})

    app.run()

    assert not app.exception
    assert any(button.label == "Download scenario JSON" for button in app.get("download_button"))


def test_shared_scenario_falls_back_for_retired_record_and_unknown_region():
    scenario = Scenario.create(
        product_id="retired-product-id",
        region="Mars research grid",
        theme="dark",
        source_snapshot="older-snapshot",
        model_version="older-model",
        scoring_version="4.0",
        inputs={
            "name": "Portable old phone",
            "manufacturer": "Example",
            "category": "Smartphone",
            "grid_kg_co2_per_kwh": 0.12,
            "daily_hours": 3.0,
            "lifespan_years": 6.0,
        },
        overrides={"daily_hours": 3.0},
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params.update({"tab": "analyse", "scenario": scenario.to_token()})

    app.run()

    assert not app.exception
    assert app.session_state["analysis_input_mode"] == "Any device estimate"
    assert app.session_state["analysis_region"] == "Custom intensity"
    assert any("original catalog record is not in the current snapshot" in warning.value for warning in app.warning)
    assert any(header.value == "Portable old phone" for header in app.header)


def test_theme_toggle_can_override_a_shared_scenario_default():
    scenario = Scenario.create(
        region="India",
        theme="dark",
        source_snapshot="snapshot",
        model_version="model",
        scoring_version="4.1",
        inputs={"name": "Phone", "manufacturer": "Example", "category": "Smartphone"},
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params.update({"tab": "analyse", "scenario": scenario.to_token()})
    app.run()
    assert not app.exception
    assert app.session_state["ui_theme"] == "dark"

    next(toggle for toggle in app.toggle if toggle.label == "Dark mode").set_value(False).run()

    assert not app.exception
    assert app.session_state["ui_theme"] == "light"
    assert app.query_params["theme"] == ["light"]


def test_product_scenario_cannot_spoof_catalog_identity_or_observed_fields():
    scenario = Scenario.create(
        product_id="ifixit-149",
        region="India",
        theme="dark",
        source_snapshot="untrusted-snapshot",
        model_version="untrusted-model",
        scoring_version="4.1",
        inputs={
            "name": "Spoofed identity",
            "manufacturer": "Spoofed maker",
            "category": "Smartphone",
            "repairability": 0.0,
        },
    )
    app = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30)
    app.query_params.update({"tab": "analyse", "scenario": scenario.to_token()})

    app.run()

    assert not app.exception
    assert any(header.value == "iPhone 16 Pro" for header in app.header)
    assert all(header.value != "Spoofed identity" for header in app.header)
    rendered = "\n".join(markdown.value for markdown in app.markdown)
    assert '<span>Product identity</span><span class="observed">Observed</span>' in rendered
    assert '<span>Repairability</span><span class="observed">Observed</span>' in rendered
    assert next(slider for slider in app.slider if slider.label == "Repairability").value == 7.0
    assert "Catalog-backed identity" in rendered
