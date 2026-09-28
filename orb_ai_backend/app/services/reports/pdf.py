"""ReportLab-based PDF report builder (Module 8).

Modular templates: cover, summary, tables, charts, appendix. Every report
type produces a professional PDF with header/footer/page-numbers.
"""
from __future__ import annotations

import io
from datetime import datetime
from typing import Any

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    PageBreak,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from app.core.config import settings
from app.services.reports.data_layer import ReportContext

BRAND = colors.HexColor("#0F172A")   # slate-900
ACCENT = colors.HexColor("#2563EB")  # blue-600
MUTED = colors.HexColor("#64748B")


class _NumberedCanvas(canvas.Canvas):
    """Add page X of Y + footer to every page."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict[str, Any]] = []

    def showPage(self) -> None:
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total_pages: int) -> None:
        self.setFillColor(MUTED)
        self.setFont("Helvetica", 8)
        text = f"{settings.REPORTS_COMPANY_NAME} · v{settings.APP_VERSION} · Generated {datetime.utcnow().isoformat(timespec='seconds')}Z"
        self.drawString(1.5 * cm, 1.0 * cm, text)
        page_str = f"Page {self._pageNumber} of {total_pages}"
        self.drawRightString(A4[0] - 1.5 * cm, 1.0 * cm, page_str)


class PDFReportBuilder:
    """Build a PDF from a ReportContext."""

    def __init__(self) -> None:
        self.styles = getSampleStyleSheet()
        self.styles.add(ParagraphStyle(
            name="H1", fontName="Helvetica-Bold", fontSize=22,
            textColor=BRAND, spaceAfter=8,
        ))
        self.styles.add(ParagraphStyle(
            name="H2", fontName="Helvetica-Bold", fontSize=14,
            textColor=BRAND, spaceAfter=6, spaceBefore=12,
        ))
        self.styles.add(ParagraphStyle(
            name="Muted", fontName="Helvetica", fontSize=9,
            textColor=MUTED, spaceAfter=4,
        ))
        self.styles.add(ParagraphStyle(
            name="Body", fontName="Helvetica", fontSize=10, leading=13,
        ))

    # ---- Public entry ---------------------------------------------------
    def build(self, ctx: ReportContext) -> bytes:
        buf = io.BytesIO()

        def _header(cvs: canvas.Canvas, doc: BaseDocTemplate) -> None:
            cvs.saveState()
            cvs.setFillColor(BRAND)
            cvs.rect(0, A4[1] - 1.6 * cm, A4[0], 1.6 * cm, stroke=0, fill=1)
            cvs.setFillColor(colors.white)
            cvs.setFont("Helvetica-Bold", 14)
            cvs.drawString(1.5 * cm, A4[1] - 1.05 * cm, settings.REPORTS_COMPANY_NAME)
            cvs.setFont("Helvetica", 9)
            cvs.drawString(1.5 * cm, A4[1] - 1.4 * cm, settings.REPORTS_COMPANY_TAGLINE)
            cvs.drawRightString(A4[0] - 1.5 * cm, A4[1] - 1.05 * cm, ctx.report_type.upper())
            cvs.drawRightString(A4[0] - 1.5 * cm, A4[1] - 1.4 * cm, ctx.user_email)
            cvs.restoreState()

        doc = BaseDocTemplate(
            buf,
            pagesize=A4,
            leftMargin=1.5 * cm, rightMargin=1.5 * cm,
            topMargin=2.2 * cm, bottomMargin=1.6 * cm,
            title=f"ORB AI {ctx.report_type} Report",
            author=settings.REPORTS_COMPANY_NAME,
        )
        frame = Frame(
            doc.leftMargin, doc.bottomMargin,
            doc.width, doc.height,
            id="body",
        )
        doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=_header)])

        story: list[Any] = []
        story.extend(self._cover(ctx))
        story.append(PageBreak())
        story.extend(self._portfolio_section(ctx))
        story.extend(self._analytics_section(ctx))
        story.extend(self._risk_section(ctx))
        if ctx.backtest:
            story.append(PageBreak())
            story.extend(self._backtest_section(ctx))
        story.append(PageBreak())
        story.extend(self._trades_section(ctx))

        doc.build(story, canvasmaker=_NumberedCanvas)
        return buf.getvalue()

    # ---- Sections -------------------------------------------------------
    def _cover(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = []
        story.append(Spacer(1, 3 * cm))
        story.append(Paragraph(f"{ctx.report_type.title()} Report", s["H1"]))
        story.append(Paragraph(
            f"Generated {ctx.generated_at.strftime('%Y-%m-%d %H:%M:%S UTC')}",
            s["Muted"],
        ))
        if ctx.period_start and ctx.period_end:
            story.append(Paragraph(
                f"Period: {ctx.period_start.strftime('%Y-%m-%d')} → {ctx.period_end.strftime('%Y-%m-%d')}",
                s["Muted"],
            ))
        story.append(Spacer(1, 12))
        story.append(Paragraph(f"User: <b>{ctx.user_email}</b>", s["Body"]))
        story.append(Spacer(1, 24))
        # Summary KPI table
        kv = ctx.portfolio_summary or {}
        rows = [
            ["Equity", _fmt_money(kv.get("equity", 0))],
            ["Net P&L", _fmt_money(kv.get("total_pnl", 0))],
            ["Realized P&L", _fmt_money(kv.get("realized_pnl", 0))],
            ["Unrealized P&L", _fmt_money(kv.get("unrealized_pnl", 0))],
            ["Open Positions", str(kv.get("open_positions", 0))],
        ]
        story.append(_table(rows))
        return story

    def _portfolio_section(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = [Paragraph("Portfolio Summary", s["H2"])]
        story.append(_kv_table(ctx.portfolio_summary))
        story.append(Paragraph("Holdings", s["H2"]))
        if ctx.holdings:
            headers = ["Symbol", "Qty", "Avg Price", "LTP", "Value", "Unrealized P&L"]
            data = [headers] + [[
                h["symbol"], f'{h["quantity"]:.2f}', _fmt_money(h["average_price"]),
                _fmt_money(h["last_price"]), _fmt_money(h["market_value"]),
                _fmt_money(h["unrealized_pnl"]),
            ] for h in ctx.holdings]
            story.append(_data_table(data))
        else:
            story.append(Paragraph("No open holdings.", s["Muted"]))
        return story

    def _analytics_section(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = [Paragraph("Performance & Analytics", s["H2"])]
        story.append(_kv_table(ctx.analytics_summary))
        if ctx.monthly_perf:
            story.append(Paragraph("Monthly Performance", s["H2"]))
            headers = ["Month", "P&L", "Trades", "Win Rate"]
            data = [headers] + [[
                m["month"], _fmt_money(m["pnl"]), str(m["trades"]),
                f'{m["win_rate"] * 100:.1f}%',
            ] for m in ctx.monthly_perf]
            story.append(_data_table(data))
        return story

    def _risk_section(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = [Paragraph("Risk Metrics", s["H2"])]
        risk = ctx.risk or {}
        margin = risk.get("margin", {})
        story.append(_kv_table(margin))
        exp = risk.get("exposure_by_symbol", [])
        if exp:
            story.append(Paragraph("Exposure by Symbol", s["H2"]))
            headers = ["Symbol", "Long", "Short", "Gross", "Net"]
            data = [headers] + [[
                e["symbol"], _fmt_money(e["long_value"]), _fmt_money(e["short_value"]),
                _fmt_money(e["gross_value"]), _fmt_money(e["net_value"]),
            ] for e in exp]
            story.append(_data_table(data))
        return story

    def _backtest_section(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = [Paragraph("Backtest Summary", s["H2"])]
        story.append(_kv_table({
            "strategy": ctx.backtest["strategy_name"],
            "symbols": ", ".join(ctx.backtest["symbols"]),
            "start": ctx.backtest["start_date"],
            "end": ctx.backtest["end_date"],
            "initial_capital": ctx.backtest["initial_capital"],
            "status": ctx.backtest["status"],
            "trade_count": ctx.backtest["trade_count"],
        }))
        if ctx.backtest.get("summary"):
            story.append(Paragraph("Metrics", s["H2"]))
            story.append(_kv_table(ctx.backtest["summary"]))
        return story

    def _trades_section(self, ctx: ReportContext) -> list[Any]:
        s = self.styles
        story: list[Any] = [Paragraph("Trade History", s["H2"])]
        if not ctx.trades:
            story.append(Paragraph("No trades in the selected period.", s["Muted"]))
            return story
        headers = ["Executed", "Symbol", "Side", "Qty", "Price", "P&L", "Strategy"]
        data = [headers]
        for t in ctx.trades[:1000]:  # cap for a printable report
            data.append([
                (t["executed_at"] or "")[:19],
                t["symbol"], t["side"], f'{t["quantity"]:.2f}',
                _fmt_money(t["price"]), _fmt_money(t["pnl"]),
                t["strategy"] or "",
            ])
        story.append(_data_table(data))
        if len(ctx.trades) > 1000:
            story.append(Paragraph(
                f"Showing 1000 of {len(ctx.trades)} trades. Use CSV export for full history.",
                s["Muted"],
            ))
        return story


# ---- helpers ------------------------------------------------------------
def _fmt_money(v: Any) -> str:
    try:
        return f"{float(v):,.2f}"
    except (TypeError, ValueError):
        return str(v)


def _table(rows: list[list[Any]]) -> Table:
    t = Table(rows, colWidths=[6 * cm, 6 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
        ("LINEBELOW", (0, 0), (-1, -1), 0.25, colors.lightgrey),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return t


def _kv_table(d: dict[str, Any]) -> Table:
    rows = [[k.replace("_", " ").title(), _fmt(v)] for k, v in (d or {}).items()]
    if not rows:
        rows = [["(no data)", ""]]
    return _table(rows)


def _fmt(v: Any) -> str:
    if isinstance(v, float):
        return _fmt_money(v)
    if isinstance(v, int):
        return str(v)
    return str(v)


def _data_table(data: list[list[Any]]) -> Table:
    t = Table(data, repeatRows=1, colWidths=None)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), BRAND),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.whitesmoke, colors.white]),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.lightgrey),
    ]))
    return t
