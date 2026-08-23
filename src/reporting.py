"""Branded PDF exports for individual assessments and saved comparisons."""
from __future__ import annotations

from io import BytesIO
from typing import Iterable

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from src.utils import Assessment, impact_category


INK = colors.HexColor("#14241c")
GREEN = colors.HexColor("#16734a")
MINT = colors.HexColor("#e7f7ee")
LINE = colors.HexColor("#dbe8df")
MUTED = colors.HexColor("#63736a")


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
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm, topMargin=18 * mm, bottomMargin=20 * mm, title=f"Impact report · {values.get('name', 'Gadget')}")
    style = _styles()
    story = [
        Paragraph("LUMA / GADGET IMPACT INTELLIGENCE", style["Brand"]),
        Paragraph(str(values.get("name", "Gadget assessment")), style["Hero"]),
        Paragraph(f"{values.get('manufacturer', 'Unknown')} · {values.get('category', 'Gadget')} · {values.get('model_number', '')}", style["Subtle"]),
        Spacer(1, 7 * mm),
    ]
    score_table = Table(
        [
            ["ECO SCORE", "LIFECYCLE CARBON", "ANNUAL ENERGY", "DATA CONFIDENCE"],
            [f"{assessment.eco_score:.1f} / 100", f"{assessment.lifecycle_carbon:,.1f} kg CO₂e", f"{assessment.annual_energy:,.1f} kWh", f"{assessment.confidence}%"],
            [f"{impact_category(assessment.eco_score)} · range {assessment.score_low:.0f}–{assessment.score_high:.0f}", f"± {assessment.uncertainty:,.1f} kg", f"Grid {assessment.grid_factor * 1000:.0f} g/kWh", "Coverage, not probability"],
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
    story.extend([score_table, Paragraph("What the model sees", style["Section"]), Paragraph(explanation, style["Body2"])])
    factor_rows = [["Lifecycle factor", "Modeled burden / 100"]] + [[name, f"{value:.1f}"] for name, value in sorted(assessment.factors.items(), key=lambda item: item[1], reverse=True)]
    factor_table = Table(factor_rows, colWidths=[130 * mm, 44 * mm])
    factor_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]), ("FONTSIZE", (0, 0), (-1, -1), 8.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.extend([Paragraph("Factor breakdown", style["Section"]), factor_table, Paragraph("Practical next steps", style["Section"])])
    for recommendation in recommendations:
        story.append(Paragraph(f"• {recommendation}", style["Body2"]))
    story.extend(
        [
            Paragraph("Evidence and boundary", style["Section"]),
            Paragraph(
                f"Source: {values.get('source_name', 'User scenario')}. Observed product data is combined with disclosed category and user assumptions. "
                "The uncertainty range reflects model holdout error and evidence coverage; it does not capture every supplier or end-of-life pathway.",
                style["Body2"],
            ),
            Paragraph(str(values.get("source_url", "")), style["Subtle"]),
        ]
    )
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()


def comparison_pdf(records: list[dict]) -> bytes:
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, rightMargin=14 * mm, leftMargin=14 * mm, topMargin=16 * mm, bottomMargin=20 * mm, title="Saved gadget comparison")
    style = _styles()
    story = [Paragraph("LUMA / SAVED COMPARISON", style["Brand"]), Paragraph("Your gadget shortlist", style["Hero"]), Paragraph("Scores are scenarios. Compare evidence confidence and source coverage alongside the ranking.", style["Subtle"]), Spacer(1, 6 * mm)]
    rows = [["Product", "Category", "Eco", "Range", "Carbon", "Energy", "Confidence"]]
    for record in records:
        rows.append(
            [
                Paragraph(str(record.get("product", "")), style["Subtle"]),
                record.get("category", ""),
                f"{record.get('eco_score', 0):.1f}",
                f"{record.get('score_low', 0):.0f}–{record.get('score_high', 0):.0f}",
                f"{record.get('lifecycle_carbon', 0):.0f} kg",
                f"{record.get('annual_energy', 0):.1f}",
                f"{record.get('confidence', 0)}%",
            ]
        )
    table = Table(rows, colWidths=[48 * mm, 27 * mm, 15 * mm, 18 * mm, 23 * mm, 21 * mm, 21 * mm], repeatRows=1)
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, 0), GREEN), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"), ("GRID", (0, 0), (-1, -1), 0.4, LINE), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f7faf8")]), ("FONTSIZE", (0, 0), (-1, -1), 7.5), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"), ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6)]))
    story.append(table)
    document.build(story, onFirstPage=_footer, onLaterPages=_footer)
    return buffer.getvalue()
