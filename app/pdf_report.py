"""Render an Analysis to a PDF (Pro feature)."""
from __future__ import annotations

import io
from datetime import datetime

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
)

from app.analysis import Analysis


def render_pdf(a: Analysis, portfolio_name: str = "My Portfolio") -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=letter, topMargin=0.6*inch, bottomMargin=0.6*inch)
    styles = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=styles["Heading1"], textColor=colors.HexColor("#0f172a"))
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=colors.HexColor("#0f172a"))
    body = styles["BodyText"]
    small = ParagraphStyle("small", parent=body, fontSize=9, textColor=colors.HexColor("#475569"))

    story = []
    story.append(Paragraph("Finance Buddy — Portfolio Report", h1))
    story.append(Paragraph(f"<b>{portfolio_name}</b> · Generated {datetime.now().strftime('%Y-%m-%d %H:%M')}", small))
    story.append(Spacer(1, 0.2*inch))

    story.append(Paragraph(f"Diversification Score: <b>{a.score}/100</b> — {a.score_band}", h2))
    story.append(Paragraph(f"Total portfolio value: <b>${a.total_value:,.2f}</b>", body))
    story.append(Paragraph(f"Magnificent 7 effective exposure: <b>{a.mag7_exposure*100:.1f}%</b>", body))
    story.append(Paragraph(f"Herfindahl Index (HHI): <b>{a.hhi:.4f}</b> (lower = more diversified)", body))
    story.append(Spacer(1, 0.15*inch))

    if a.insights:
        story.append(Paragraph("Educational Insights", h2))
        for ins in a.insights:
            story.append(Paragraph(f"• {ins}", body))
        story.append(Spacer(1, 0.15*inch))

    def _table(title, rows):
        story.append(Paragraph(title, h2))
        t = Table(rows, colWidths=[3*inch, 1.5*inch])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#0f172a")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("FONTNAME", (0,0), (-1,0), "Helvetica-Bold"),
            ("GRID", (0,0), (-1,-1), 0.25, colors.HexColor("#cbd5e1")),
            ("ALIGN", (1,1), (1,-1), "RIGHT"),
        ]))
        story.append(t)
        story.append(Spacer(1, 0.2*inch))

    _table("Positions",
        [["Ticker", "Weight"]] +
        [[p["ticker"], f'{p["weight"]*100:.2f}%'] for p in a.positions])

    _table("Asset Class Mix",
        [["Asset Class", "Weight"]] +
        [[k, f"{v*100:.1f}%"] for k, v in sorted(a.asset_class_mix.items(), key=lambda x: -x[1])])

    _table("Sector Mix (look-through)",
        [["Sector", "Weight"]] +
        [[k, f"{v*100:.1f}%"] for k, v in sorted(a.sector_mix.items(), key=lambda x: -x[1])])

    _table("Top Single-Stock Exposure (after ETF look-through)",
        [["Ticker", "Effective Weight"]] +
        [[t, f"{w*100:.2f}%"] for t, w in a.single_stock_top])

    story.append(PageBreak())
    story.append(Paragraph("Disclaimer", h2))
    story.append(Paragraph(
        "Finance Buddy is an educational tool. The data and analysis presented are for "
        "informational purposes only and do not constitute investment, financial, tax, or legal "
        "advice. Past performance is not indicative of future results. Consult a licensed "
        "financial advisor before making investment decisions. ETF look-through data is a "
        "periodic snapshot and may not reflect current holdings.",
        body
    ))

    doc.build(story)
    return buf.getvalue()
