import pandas as pd
import pytest

from src.exporting import ExportError, MAX_CSV_CELL_CHARS, csv_bytes, spreadsheet_safe_value
from src.reporting import MAX_COMPARISON_RECORDS, ReportError, assessment_pdf, comparison_pdf
from src.utils import calculate_assessment


def _scenario(name: str = "Device") -> dict:
    return {
        "name": name,
        "model_number": "<& model>",
        "manufacturer": "A&B <Labs>",
        "category": "Laptop",
        "source_name": "Source & evidence",
        "source_url": "https://example.com/?a=1&b=2",
        "manufacturing_kg": 220.0,
        "active_power_w": 45.0,
        "daily_hours": 6.0,
        "grid_kg_co2_per_kwh": 0.42,
        "lifespan_years": 5.0,
        "repairability": 6.0,
        "recyclability_pct": 70.0,
        "recycled_content_pct": 30.0,
        "battery_wh": 60.0,
        "replaceable_battery": False,
        "weight_kg": 1.5,
        "transport_km": 7000.0,
        "catalog_product": False,
    }


def test_csv_export_neutralizes_spreadsheet_formulas():
    assert spreadsheet_safe_value("=HYPERLINK(\"bad\")").startswith("'=")
    assert spreadsheet_safe_value("  +SUM(1,2)").startswith("'")
    assert spreadsheet_safe_value("\ufeff=HYPERLINK(\"bad\")").startswith("'")
    assert spreadsheet_safe_value("\u202e@danger").startswith("'")
    assert spreadsheet_safe_value("ordinary text") == "ordinary text"
    exported = csv_bytes(pd.DataFrame({"name": ["@danger", "Safe"], "score": [1, 2]}))
    assert exported.startswith(b"\xef\xbb\xbf")
    assert b"'@danger" in exported


def test_pdf_exports_escape_untrusted_reportlab_markup():
    values = _scenario("<b>Not markup</b> & device")
    result = calculate_assessment(values)
    single = assessment_pdf(values, result, "Use <unknown> & verify.", ["Repair <first> & reuse"])
    comparison = comparison_pdf(
        [
            {
                "product": "<b>Not markup</b> & device",
                "category": "Laptop & tablet",
                "eco_score": result.eco_score,
                "score_low": result.score_low,
                "score_high": result.score_high,
                "lifecycle_carbon": result.lifecycle_carbon,
                "annual_energy": result.annual_energy,
                "confidence": result.confidence,
            }
        ]
    )
    assert single.startswith(b"%PDF") and len(single) > 2_000
    assert comparison.startswith(b"%PDF") and len(comparison) > 1_500


def test_exports_reject_pathological_sizes_and_malformed_pdf_numbers():
    with pytest.raises(ExportError, match="cell exceeds"):
        csv_bytes(pd.DataFrame({"name": ["x" * (MAX_CSV_CELL_CHARS + 1)]}))
    with pytest.raises(ReportError, match="exceeds 50"):
        comparison_pdf([{}] * (MAX_COMPARISON_RECORDS + 1))

    # Untrusted imported project records may contain strings where numeric
    # values are expected; PDF generation must remain deterministic and safe.
    malformed = comparison_pdf([{"product": "A", "eco_score": "not-a-number", "confidence": float("inf")}])
    assert malformed.startswith(b"%PDF")


def test_pdf_recommendation_iterable_is_bounded():
    values = _scenario()
    result = calculate_assessment(values)

    def endless():
        while True:
            yield "repair"

    with pytest.raises(ReportError, match="recommendations"):
        assessment_pdf(values, result, "Explanation", endless())
