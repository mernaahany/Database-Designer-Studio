"""
utils/pdf_report.py - Generate a PDF report of all session modifications
"""
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, KeepTogether
)
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from typing import List
import io
from datetime import datetime
from ..state import ModificationRecord




# ── Colour palette ────────────────────────────────────────────────────────────
PRIMARY   = colors.HexColor("#1E3A5F")
SECONDARY = colors.HexColor("#2E86AB")
ACCENT    = colors.HexColor("#E8F4F8")
CODE_BG   = colors.HexColor("#F5F5F5")
DARK_TEXT = colors.HexColor("#1A1A2E")


def _build_styles():
    base = getSampleStyleSheet()
 
    title_style = ParagraphStyle(
        "ReportTitle", parent=base["Title"],
        fontSize=24, textColor=PRIMARY, spaceAfter=6,
        fontName="Helvetica-Bold", alignment=TA_CENTER,
    )
    subtitle_style = ParagraphStyle(
        "ReportSubtitle", parent=base["Normal"],
        fontSize=11, textColor=SECONDARY, spaceAfter=20,
        fontName="Helvetica", alignment=TA_CENTER,
    )
    section_style = ParagraphStyle(
        "SectionHeader", parent=base["Heading2"],
        fontSize=13, textColor=PRIMARY, spaceBefore=16, spaceAfter=6,
        fontName="Helvetica-Bold", borderPad=4,
    )
    body_style = ParagraphStyle(
        "BodyText", parent=base["Normal"],
        fontSize=10, textColor=DARK_TEXT, spaceAfter=4,
        fontName="Helvetica", leading=14,
    )
    code_style = ParagraphStyle(
        "CodeText", parent=base["Code"],
        fontSize=9, fontName="Courier", textColor=colors.HexColor("#333333"),
        backColor=CODE_BG, borderColor=colors.HexColor("#CCCCCC"),
        borderWidth=0.5, borderPad=4, spaceAfter=4, leading=12,
    )
    label_style = ParagraphStyle(
        "Label", parent=base["Normal"],
        fontSize=9, textColor=SECONDARY, spaceAfter=2,
        fontName="Helvetica-Bold",
    )
    return {
        "title": title_style, "subtitle": subtitle_style,
        "section": section_style, "body": body_style,
        "code": code_style, "label": label_style,
    }


def generate_pdf_report(
    modification_history: List[ModificationRecord],
    db_schema: str,
    db_name: str = "database.db",
) -> bytes:
    """
    Generate a PDF report summarising all modifications made in this session.
    Returns the PDF as bytes (ready for Streamlit download button).
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=letter,
        leftMargin=0.85 * inch, rightMargin=0.85 * inch,
        topMargin=1 * inch, bottomMargin=0.85 * inch,
        title="Database Modification Report",
        author="DB Designer System"
    )

    styles = _build_styles()
    story  = []
    now    = datetime.now().strftime("%B %d, %Y  %H:%M")

    # Cover
    story.append(Spacer(1, 0.4 * inch))
    story.append(Paragraph("Database Modification Report", styles["title"]))
    story.append(Paragraph(f"Generated on {now}", styles["subtitle"]))
    story.append(Paragraph(f"Database: <b>{db_name}</b>", styles["subtitle"]))
    story.append(HRFlowable(width="100%", thickness=2, color=PRIMARY, spaceAfter=20))

    # Summary table
    story.append(Paragraph("Summary", styles["section"]))
    total_sql = sum(len(r["sql_statements"]) for r in modification_history)
    summary_data = [
        ["Total Modifications", str(len(modification_history))],
        ["Total SQL Statements", str(total_sql)],
        ["Database File", db_name],
        ["Session Date", now],
    ]
    t = Table(summary_data, colWidths=[2.5 * inch, 4.5 * inch])
    t.setStyle(TableStyle([
        ("BACKGROUND",  (0, 0), (0, -1), ACCENT),
        ("TEXTCOLOR",   (0, 0), (0, -1), PRIMARY),
        ("FONTNAME",    (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",    (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE",    (0, 0), (-1, -1), 10),
        ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, ACCENT]),
        ("GRID",        (0, 0), (-1, -1), 0.5, colors.HexColor("#CCCCCC")),
        ("PADDING",     (0, 0), (-1, -1), 6),
    ]))
    story.append(t)
    story.append(Spacer(1, 0.25 * inch))

    # Modification details
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceAfter=8))
    story.append(Paragraph("Modification Details", styles["section"]))
 
    if not modification_history:
        story.append(Paragraph("No modifications were applied in this session.", styles["body"]))
    else:
        for i, rec in enumerate(modification_history, 1):
            block = []
 
            # Header row
            header_data = [[
                Paragraph(f"#{i}  {rec['timestamp']}", ParagraphStyle(
                    "H", fontName="Helvetica-Bold", fontSize=11,
                    textColor=colors.white,
                )),
            ]]
            ht = Table(header_data, colWidths=[6.8 * inch])
            ht.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PRIMARY),
                ("PADDING",    (0, 0), (-1, -1), 6),
            ]))
            block.append(ht)
 
            # User request
            block.append(Spacer(1, 4))
            block.append(Paragraph("User Request:", styles["label"]))
            block.append(Paragraph(rec["user_request"], styles["body"]))
 
            # Description
            block.append(Paragraph("Changes Applied:", styles["label"]))
            block.append(Paragraph(rec["description"], styles["body"]))
 
            # SQL statements
            block.append(Paragraph("SQL Executed:", styles["label"]))
            for sql in rec["sql_statements"]:
                # Escape XML special chars for ReportLab
                safe = sql.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
                block.append(Paragraph(safe, styles["code"]))
 
            block.append(Spacer(1, 0.2 * inch))
            story.append(KeepTogether(block))

    # Final schema 
    story.append(HRFlowable(width="100%", thickness=1, color=SECONDARY, spaceAfter=8))
    story.append(Paragraph("Final Database Schema", styles["section"]))
    schema_lines = db_schema.split("\n")
    for line in schema_lines:
        if line.startswith("TABLE:") or line.startswith("VIEW:"):
            story.append(Paragraph(line, styles["label"]))
        elif line.strip():
            safe = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            story.append(Paragraph(safe, styles["code"]))
        else:
            story.append(Spacer(1, 4))
 
    # Footer note
    story.append(Spacer(1, 0.4 * inch))
    story.append(HRFlowable(width="100%", thickness=1, color=colors.HexColor("#CCCCCC")))
    story.append(Paragraph(
        "This report was auto-generated by the Database Designer Modification Module.",
        ParagraphStyle("Footer", fontName="Helvetica", fontSize=8,
                       textColor=colors.gray, alignment=TA_CENTER),
    ))
 
    doc.build(story)
    return buf.getvalue()

