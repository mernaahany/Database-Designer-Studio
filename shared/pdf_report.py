"""
PDF Report Generator for Validation Reports

"""
from __future__ import annotations
import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from Features.Feature1_create_db.models import ValidationResult, DatabaseSchema

logger = logging.getLogger(__name__)


def generate_validation_pdf(
    validation_result: Any,
    schema: Any,
    output_path: str | Path,
    workspace_id: str = "",
) -> str:
    """
    Generate a PDF validation report.
    
    Args:
        validation_result: ValidationResult object
        schema: DatabaseSchema object
        output_path: Path to save the PDF
        workspace_id: Workspace identifier
        
    Returns:
        Path to the generated PDF file
    """
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate,
            Table,
            TableStyle,
            Paragraph,
            Spacer,
            PageBreak,
            KeepTogether,
        )
        from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
    except ImportError:
        logger.error("reportlab not installed. Cannot generate PDF report.")
        raise ImportError(
            "reportlab is required for PDF generation. "
            "Install it with: pip install reportlab"
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Create PDF document
    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=letter,
        rightMargin=0.75 * inch,
        leftMargin=0.75 * inch,
        topMargin=0.75 * inch,
        bottomMargin=0.75 * inch,
    )

    # Styles
    styles = getSampleStyleSheet()
    
    title_style = ParagraphStyle(
        "CustomTitle",
        parent=styles["Heading1"],
        fontSize=24,
        textColor=colors.HexColor("#1e3a5f"),
        spaceAfter=12,
        alignment=TA_CENTER,
        fontName="Helvetica-Bold",
    )
    
    heading_style = ParagraphStyle(
        "CustomHeading",
        parent=styles["Heading2"],
        fontSize=16,
        textColor=colors.HexColor("#4fc3f7"),
        spaceAfter=10,
        spaceBefore=12,
        fontName="Helvetica-Bold",
    )
    
    subheading_style = ParagraphStyle(
        "CustomSubHeading",
        parent=styles["Heading3"],
        fontSize=13,
        textColor=colors.HexColor("#2d5f7f"),
        spaceAfter=8,
        spaceBefore=10,
        fontName="Helvetica-Bold",
    )
    
    body_style = ParagraphStyle(
        "CustomBody",
        parent=styles["BodyText"],
        fontSize=10,
        textColor=colors.black,
        spaceAfter=6,
        alignment=TA_JUSTIFY,
    )

    # Build content
    story = []

    # Title
    story.append(Paragraph("Database Schema Validation Report", title_style))
    story.append(Spacer(1, 0.2 * inch))

    # Metadata
    metadata = [
        ["Report Generated:", datetime.now().strftime("%Y-%m-%d %H:%M:%S")],
        ["Workspace ID:", workspace_id or "N/A"],
        ["Schema Status:", "✓ Valid" if validation_result.is_valid else "⚠ Issues Found"],
    ]
    
    metadata_table = Table(metadata, colWidths=[2 * inch, 4 * inch])
    metadata_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f0f0f0")),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (1, 0), (1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
    ]))
    story.append(metadata_table)
    story.append(Spacer(1, 0.3 * inch))

    # Schema Overview
    story.append(Paragraph("Schema Overview", heading_style))
    
    if schema:
        total_tables = len(schema.tables)
        total_columns = sum(len(t.columns) for t in schema.tables)
        
        overview_data = [
            ["Total Tables:", str(total_tables)],
            ["Total Columns:", str(total_columns)],
            ["Normalization Level:", getattr(schema, "normalization_level", "N/A")],
        ]
        
        overview_table = Table(overview_data, colWidths=[2 * inch, 4 * inch])
        overview_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#e8f4f8")),
            ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
            ("ALIGN", (0, 0), (-1, -1), "LEFT"),
            ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 10),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ]))
        story.append(overview_table)
        story.append(Spacer(1, 0.2 * inch))

    # Validation Summary
    story.append(Paragraph("Validation Summary", heading_style))
    
    if validation_result.is_valid:
        story.append(Paragraph(
            "✓ <b>Schema validation passed successfully.</b> No critical issues detected.",
            body_style
        ))
    else:
        story.append(Paragraph(
            "⚠ <b>Schema validation found issues.</b> Please review the details below.",
            body_style
        ))
    
    story.append(Spacer(1, 0.2 * inch))

    # Issues Breakdown
    if validation_result.issues:
        errors = [i for i in validation_result.issues if i.severity == "error"]
        warnings = [i for i in validation_result.issues if i.severity == "warning"]
        suggestions = [i for i in validation_result.issues if i.severity == "info"]
        
        summary_data = [
            ["Issue Type", "Count"],
            ["Errors", str(len(errors))],
            ["Warnings", str(len(warnings))],
            ["Suggestions", str(len(suggestions))],
        ]
        
        summary_table = Table(summary_data, colWidths=[3 * inch, 3 * inch])
        summary_table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4fc3f7")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, 0), 11),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 1), (-1, -1), colors.HexColor("#f9f9f9")),
            ("GRID", (0, 0), (-1, -1), 1, colors.grey),
        ]))
        story.append(summary_table)
        story.append(Spacer(1, 0.3 * inch))

        # Detailed Issues
        if errors:
            story.append(Paragraph("Errors", subheading_style))
            for issue in errors:
                location = f"[{issue.table}]" if issue.table else "Schema"
                if issue.column:
                    location += f".{issue.column}"
                story.append(Paragraph(
                    f"<b>{location}</b>: {issue.message}",
                    body_style
                ))
            story.append(Spacer(1, 0.15 * inch))

        if warnings:
            story.append(Paragraph("Warnings", subheading_style))
            for issue in warnings:
                location = f"[{issue.table}]" if issue.table else "Schema"
                if issue.column:
                    location += f".{issue.column}"
                story.append(Paragraph(
                    f"<b>{location}</b>: {issue.message}",
                    body_style
                ))
            story.append(Spacer(1, 0.15 * inch))

        if suggestions:
            story.append(Paragraph("Suggestions for Improvement", subheading_style))
            for issue in suggestions:
                location = f"[{issue.table}]" if issue.table else "Schema"
                if issue.column:
                    location += f".{issue.column}"
                
                suggestion_text = f"<b>{location}</b>: {issue.message}"
                if hasattr(issue, 'suggestion') and issue.suggestion:
                    suggestion_text += f"<br/><i>→ Suggestion: {issue.suggestion}</i>"
                
                story.append(Paragraph(suggestion_text, body_style))
            story.append(Spacer(1, 0.15 * inch))

    # LLM Suggestions
    if hasattr(validation_result, 'llm_suggestions') and validation_result.llm_suggestions:
        story.append(PageBreak())
        story.append(Paragraph("AI-Generated Recommendations", heading_style))
        
        for idx, suggestion in enumerate(validation_result.llm_suggestions, 1):
            location = ""
            if hasattr(suggestion, 'table') and suggestion.table:
                location = f"[{suggestion.table}]"
                if hasattr(suggestion, 'column') and suggestion.column:
                    location += f".{suggestion.column}"
            
            message = getattr(suggestion, 'message', str(suggestion))
            suggestion_detail = getattr(suggestion, 'suggestion', '')
            
            story.append(Paragraph(
                f"<b>{idx}. {location}</b> {message}",
                body_style
            ))
            if suggestion_detail:
                story.append(Paragraph(
                    f"<i>→ {suggestion_detail}</i>",
                    body_style
                ))
            story.append(Spacer(1, 0.1 * inch))

    # Schema Details
    if schema and schema.tables:
        story.append(PageBreak())
        story.append(Paragraph("Schema Details", heading_style))
        
        for table in schema.tables:
            story.append(Paragraph(f"Table: {table.name}", subheading_style))
            
            if table.description:
                story.append(Paragraph(table.description, body_style))
            
            # Column details
            col_data = [["Column", "Type", "Constraints"]]
            for col in table.columns:
                constraints = ", ".join(col.constraints) if col.constraints else "—"
                col_data.append([col.name, col.data_type, constraints])
            
            col_table = Table(col_data, colWidths=[2 * inch, 1.5 * inch, 3 * inch])
            col_table.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4fc3f7")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("ALIGN", (0, 0), (-1, -1), "LEFT"),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BACKGROUND", (0, 1), (-1, -1), colors.beige),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ]))
            story.append(col_table)
            story.append(Spacer(1, 0.2 * inch))

    # Footer
    story.append(Spacer(1, 0.5 * inch))
    story.append(Paragraph(
        "— End of Validation Report —",
        ParagraphStyle("Footer", parent=body_style, alignment=TA_CENTER, textColor=colors.grey)
    ))

    # Build PDF
    doc.build(story)
    logger.info(f"Validation PDF report generated: {output_path}")
    
    return str(output_path)