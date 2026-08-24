"""Streamlit state regressions for share links and theme changes."""
from pathlib import Path

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


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
