"""Branded PDF exports for individual assessments and saved comparisons."""
from __future__ import annotations

import html
import math
import re
from io import BytesIO
from itertools import islice
from typing import Iterable, Mapping
from urllib.parse import urlsplit

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.evidence import evidence_ledger
from src.utils import Assessment, impact_category


INK = colors.HexColor("#14241c")
GREEN = colors.HexColor("#16734a")
MINT = colors.HexColor("#e7f7ee")
LINE = colors.HexColor("#dbe8df")
MUTED = colors.HexColor("#63736a")
MAX_COMPARISON_RECORDS = 50
MAX_FACTOR_ROWS = 32
MAX_RECOMMENDATIONS = 20


class ReportError(ValueError):
    """A report input is malformed or exceeds a safe rendering boundary."""


def _plain_text(value: object, max_chars: int = 4_000) -> str:
    """Bound and clean untrusted catalogue/user text before PDF rendering."""
    raw = value if isinstance(value, str) else str(value or "")
    text = re.sub(r"[\x00-\x1f\x7f]", " ", raw[: max_chars + 1])
    text = re.sub(r"[\u200b\u202a-\u202e\u2066-\u2069\ufeff]", "", text)
    return text[:max_chars]


def _paragraph_text(value: object, max_chars: int = 4_000) -> str:
    """Escape ReportLab paragraph markup supplied by catalogues or users."""
    return html.escape(_plain_text(value, max_chars=max_chars), quote=False)


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return number if math.isfinite(number) else fallback


def _safe_source_url(value: object) -> str:
    text = _plain_text(value, 1_000).strip()
    if not text:
        return ""
    try:
        parsed = urlsplit(text)
        port = parsed.port
    except ValueError:
        return "Source URL omitted: invalid URL"
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or port not in (None, 443):
        return "Source URL omitted: only credential-free HTTPS URLs are exported"
    return text


def _bounded_recommendations(values: Iterable[str]) -> list[str]:
    if isinstance(values, (str, bytes)):
        raise ReportError("recommendations must be an iterable of strings")
    try:
        items = list(islice(iter(values), MAX_RECOMMENDATIONS + 1))
    except TypeError as exc:
        raise ReportError("recommendations must be an iterable of strings") from exc
    if len(items) > MAX_RECOMMENDATIONS:
        raise ReportError(f"report exceeds {MAX_RECOMMENDATIONS} recommendations")
    if any(not isinstance(item, str) for item in items):
        raise ReportError("every recommendation must be a string")
    return items


def _styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="Brand", parent=styles["Normal"], fontName="Helvetica-Bold", fontSize=9, textColor=GREEN, leading=12, spaceAfter=5))
    styles.add(ParagraphStyle(name="Hero", parent=styles["Title"], fontName="Helvetica-Bold", fontSize=24, leading=28, textColor=INK, spaceAfter=8))
    styles.add(ParagraphStyle(name="Subtle", parent=styles["Normal"], fontSize=8.5, leading=12, textColor=MUTED))
    styles.add(ParagraphStyle(name="Section", parent=styles["Heading2"], fontName="Helvetica-Bold", fontSize=13, leading=16, textColor=INK, spaceBefore=12, spaceAfter=7))
    styles.add(ParagraphStyle(name="Body2", parent=styles["BodyText"], fontSize=9.5, leading=14, textColor=INK, alignment=TA_LEFT))
    return styles


def _footer(canvas, document):
    canvas.saveState()
    canvas.setStrokeColor(LINE)
    canvas.line(18 * mm, 14 * mm, 192 * mm, 14 * mm)
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 9 * mm, "Luma · Environmental Impact Analyser · Decision support, not an ISO-conformant LCA")
    canvas.drawRightString(192 * mm, 9 * mm, f"Page {document.page}")
    canvas.restoreState()


def assessment_pdf(values: dict, assessment: Assessment, explanation: str, recommendations: Iterable[str]) -> bytes:
    if not isinstance(values, Mapping):
        raise ReportError("values must be a mapping")
    recommendation_items = _bounded_recommendations(recommendations)
    factors = getattr(assessment, "factors", None)
    if not isinstance(factors, Mapping):
        raise ReportError("assessment factors must be a mapping")
    factor_items = list(islice(factors.items(), MAX_FACTOR_ROWS + 1))
    if len(factor_items) > MAX_FACTOR_ROWS:
        raise ReportError(f"report exceeds {MAX_FACTOR_ROWS} lifecycle factors")
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=20 * mm, title=f"Impact report · {_plain_text(values.get('name', 'Gadget'), 160)}")
    style = _styles()
    story = [
        Paragraph("LUMA / GADGET IMPACT INTELLIGENCE", style["Brand"]),
        Paragraph(_paragraph_text(values.get("name", "Gadget assessment"), 300), style["Hero"]),
        Paragraph(
            " · ".join(
                _paragraph_text(item, 160)
                for item in (values.get("manufacturer", "Unknown"), values.get("category", "Gadget"), values.get("model_number", ""))
            ),
            style["Subtle"],
        ),
        Spacer(1, 7 * mm),
    ]
    score_table = Table(
        [
            ["ECO SCORE", "LIFECYCLE CARBON", "ANNUAL ENERGY", "DATA CONFIDENCE"],
            [f"{_number(assessment.eco_score):.1f} / 100", f"{_number(assessment.lifecycle_carbon):,.1f} kg CO₂e", f"{_number(assessment.annual_energy):,.1f} kWh", f"{int(_number(assessment.confidence))}%"],
            [f"{impact_category(_number(assessment.eco_score))} · range {_number(assessment.score_low):.0f}–{_number(assessment.score_high):.0f}", f"± {_number(assessment.uncertainty):,.1f} kg", f"Grid {_number(assessment.grid_factor) * 1000:.0f} g/kWh", "Coverage, not probability"],
        ],
        colWidths=[43.5 * mm] * 4,
        rowHeights=[8 * mm, 12 * mm, 9 * mm],
    )
    score_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), MINT),
                ("BOX", (0, 0), (-1, -1), 0.6, LINE),
                ("INNERGRID", (0, 0), (-1, -1), 0.4, LINE),
                ("TEXTCOLOR", (0, 0), (-1, 0), GREEN),
                ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, 0), 7),
                ("FONTSIZE", (0, 1), (-1, 1), 13),
                ("FONTSIZE", (0, 2), (-1, 2), 7.5),
                ("TEXTCOLOR", (0, 1), (-1, 1), INK),
                ("TEXTCOLOR", (0, 2), (-1, 2), MUTED),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
            ]
        )
    )
    story.extend([score_table, Paragraph("What the model sees", style["Section"]), Paragraph(_paragraph_text(explanation), style["Body2"])])
    factor_rows = [["Lifecycle factor", "Modeled burden / 100"]] + [
        [_plain_text(name, 120), f"{_number(value):.1f}"]
        for name, value in sorted(factor_items, key=lambda item: _number(item[1]), reverse=True)
    ]
    factor_table = Table(factor_rows, colWidths=[130 * mm, 44 * mm])
    factor_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    evidence_rows = [["Input", "Value", "Status", "Source / date"]]
    for entry in evidence_ledger(values, assessment.annual_energy):
        evidence_rows.append(
            [
                Paragraph(_paragraph_text(entry.factor, 120), style["Subtle"]),
                Paragraph(_paragraph_text(entry.value, 120), style["Subtle"]),
                Paragraph(_paragraph_text(entry.status, 80), style["Subtle"]),
                Paragraph(_paragraph_text(f"{entry.source} · {entry.date}", 240), style["Subtle"]),
            ]
        )
    evidence_table = Table(evidence_rows, colWidths=[38 * mm, 33 * mm, 30 * mm, 73 * mm], repeatRows=1)
    evidence_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), GREEN),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]),
                ("FONTSIZE", (0, 0), (-1, -1), 7.3),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    story.extend(
        [
            Paragraph("Factor breakdown", style["Section"]),
            factor_table,
            Paragraph("Evidence and assumptions", style["Section"]),
            evidence_table,
            Paragraph("Practical next steps", style["Section"]),
        ]
    )
    for recommendation in recommendation_items:
        story.append(Paragraph(f"• {_paragraph_text(recommendation, 1_000)}", style["Body2"]))
    story.extend(
        [
            Paragraph("Evidence and boundary", style["Section"]),
            Paragraph(
                f"Source: {_paragraph_text(values.get('source_name', 'User scenario'), 300)}. Observed product data is combined with disclosed category and user assumptions. "
                "The uncertainty range reflects model holdout error and evidence coverage; it does not capture every supplier or end-of-life pathway.",
                style["Body2"],
            ),
            Paragraph(_paragraph_text(_safe_source_url(values.get("source_url", "")), 1_000), style["Subtle"]),
            Paragraph(
                _paragraph_text(
                    " · ".join(
                        part
                        for part in (
                            f"Scenario {values.get('_scenario_version', 'unversioned')}",
                            f"Data {values.get('_source_snapshot', 'current snapshot')}",
                            f"Model {values.get('_model_version', 'current')}",
                            f"Scoring {values.get('_scoring_version', 'current')}",
                        )
                        if part
                    ),
                    500,
                ),
                style["Subtle"],
            ),
        ]
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def comparison_pdf(records: list[dict]) -> bytes:
    if not isinstance(records, list):
        raise ReportError("records must be a list")
    if len(records) > MAX_COMPARISON_RECORDS:
        raise ReportError(f"comparison exceeds {MAX_COMPARISON_RECORDS} records")
    if any(not isinstance(record, Mapping) for record in records):
        raise ReportError("every comparison record must be a mapping")
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=16 * mm, bottomMargin=20 * mm, title="Saved gadget comparison")
    style = _styles()
    story = [Paragraph("LUMA / SAVED COMPARISON", style["Brand"]), Paragraph("Your gadget shortlist", style["Hero"]), Paragraph("Scores are scenarios. Compare evidence confidence and source coverage alongside the ranking.", style["Subtle"]), Spacer(1, 6 * mm)]
    rows = [["Product", "Category", "Eco", "Range", "Carbon", "Energy", "Confidence"]]
    for record in records:
        rows.append(
            [
                Paragraph(_paragraph_text(record.get("product", ""), 300), style["Subtle"]),
                Paragraph(_paragraph_text(record.get("category", ""), 120), style["Subtle"]),
                f"{_number(record.get('eco_score')):.1f}",
                f"{_number(record.get('score_low')):.0f}–{_number(record.get('score_high')):.0f}",
                f"{_number(record.get('lifecycle_carbon')):.0f} kg",
                f"{_number(record.get('annual_energy')):.1f}",
                f"{int(_number(record.get('confidence')))}%",
            ]
        )
    table = Table(rows, colWidths=[48 * mm, 27 * mm, 15 * mm, 18 * mm, 23 * mm, 21 * mm, 21 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]), ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(table)
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


__all__ = [
    "MAX_COMPARISON_RECORDS",
    "MAX_FACTOR_ROWS",
    "MAX_RECOMMENDATIONS",
    "ReportError",
    "assessment_pdf",
    "comparison_pdf",
]
