"""CSV export module — reuses the same ReportContext data layer."""
from __future__ import annotations

import csv
import io
from typing import Iterable

from app.services.reports.data_layer import ReportContext


class CSVExporter:
    def build(self, ctx: ReportContext) -> bytes:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["ORB AI Report", ctx.report_type])
        w.writerow(["Generated At", ctx.generated_at.isoformat()])
        w.writerow(["User", ctx.user_email])
        w.writerow([])

        w.writerow(["-- Portfolio Summary --"])
        for k, v in ctx.portfolio_summary.items():
            w.writerow([k, v])
        w.writerow([])

        w.writerow(["-- Analytics --"])
        for k, v in ctx.analytics_summary.items():
            w.writerow([k, v])
        w.writerow([])

        w.writerow(["-- Holdings --"])
        w.writerow(["symbol", "quantity", "average_price", "last_price",
                    "market_value", "unrealized_pnl", "realized_pnl"])
        for h in ctx.holdings:
            w.writerow([h["symbol"], h["quantity"], h["average_price"],
                        h["last_price"], h["market_value"],
                        h["unrealized_pnl"], h["realized_pnl"]])
        w.writerow([])

        w.writerow(["-- Trades --"])
        w.writerow(["executed_at", "symbol", "side", "quantity", "price", "pnl", "strategy"])
        for t in ctx.trades:
            w.writerow([t["executed_at"], t["symbol"], t["side"], t["quantity"],
                        t["price"], t["pnl"], t["strategy"]])

        return buf.getvalue().encode("utf-8")

    def stream(self, ctx: ReportContext) -> Iterable[bytes]:
        """Chunked streaming (single chunk here; keeps API contract stable)."""
        yield self.build(ctx)
