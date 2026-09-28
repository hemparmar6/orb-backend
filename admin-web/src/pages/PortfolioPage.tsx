import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card, fmtDate } from '../ui';

const fmt = (v: number | undefined) =>
  new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(v ?? 0));
const pct = (v: number | undefined) => `${(Number(v ?? 0) * 100).toFixed(2)}%`;

export default function PortfolioPage() {
  const [summary, setSummary] = useState<any>(null);
  const [holdings, setHoldings] = useState<any[]>([]);
  const [analytics, setAnalytics] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([
      api.portfolioSummary().catch(() => null),
      api.portfolioHoldings().catch(() => []),
      api.analyticsSummary().catch(() => null),
    ])
      .then(([s, h, a]) => {
        setSummary(s);
        setHoldings(h || []);
        setAnalytics(a);
      })
      .catch((e) => setErr(String(e?.message || e)));
  }, []);

  if (err) return <div className="empty">Error: {err}</div>;

  return (
    <div>
      <div className="card-row" data-testid="portfolio-kpis">
        <Card title="Equity" value={`$${fmt(summary?.equity)}`} sub={`${summary?.num_sessions ?? 0} sessions`} testId="kpi-equity" />
        <Card
          title="Net P&L"
          value={<span className={Number(summary?.total_pnl ?? 0) >= 0 ? 'pos' : 'neg'}>${fmt(summary?.total_pnl)}</span>}
          testId="kpi-net-pnl"
        />
        <Card title="Realized" value={`$${fmt(summary?.realized_pnl)}`} testId="kpi-realized" />
        <Card title="Unrealized" value={`$${fmt(summary?.unrealized_pnl)}`} testId="kpi-unrealized" />
        <Card title="Exposure" value={`$${fmt(summary?.exposure)}`} testId="kpi-exposure" />
        <Card title="Open Positions" value={summary?.open_positions ?? 0} testId="kpi-open-positions" />
      </div>

      <div className="card-row" style={{ marginTop: 12 }}>
        <Card title="Win Rate" value={pct(analytics?.win_rate)} sub={`${analytics?.total_trades ?? 0} trades`} />
        <Card title="Profit Factor" value={fmt(analytics?.profit_factor)} />
        <Card title="Avg R:R" value={fmt(analytics?.average_rr)} />
        <Card title="Expectancy" value={fmt(analytics?.expectancy)} />
        <Card title="Max Drawdown" value={fmt(analytics?.max_drawdown)} sub={pct(analytics?.max_drawdown_pct)} />
      </div>

      <h2 style={{ marginTop: 24 }}>Holdings</h2>
      <div className="table-wrap" data-testid="holdings-table">
        <table className="table">
          <thead>
            <tr>
              <th>Symbol</th><th>Side</th><th>Qty</th><th>Avg Price</th>
              <th>LTP</th><th>Market Value</th><th>Unrealized</th><th>Realized</th>
            </tr>
          </thead>
          <tbody>
            {holdings.length === 0 ? (
              <tr><td colSpan={8} className="empty">No open holdings</td></tr>
            ) : holdings.map((h) => (
              <tr key={h.symbol + h.exchange}>
                <td className="mono">{h.symbol}</td>
                <td>{h.side}</td>
                <td>{h.quantity}</td>
                <td>${fmt(h.average_price)}</td>
                <td>${fmt(h.last_price)}</td>
                <td>${fmt(h.market_value)}</td>
                <td className={h.unrealized_pnl >= 0 ? 'pos' : 'neg'}>${fmt(h.unrealized_pnl)}</td>
                <td>${fmt(h.realized_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
