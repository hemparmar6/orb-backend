import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

const fmt = (v: number | undefined) =>
  new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(Number(v ?? 0));

export default function RiskPage() {
  const [dash, setDash] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.riskDashboard().then(setDash).catch((e) => setErr(String(e?.message || e)));
  }, []);

  if (err) return <div className="empty">Error: {err}</div>;
  if (!dash) return <div className="empty">Loading…</div>;

  const util = dash.margin?.utilisation ?? 0;

  return (
    <div>
      <div className="card-row">
        <Card title="Capital" value={`$${fmt(dash.margin?.capital)}`} />
        <Card title="Gross Exposure" value={`$${fmt(dash.margin?.gross_exposure)}`} />
        <Card
          title="Utilisation"
          value={<span className={util > 0.7 ? 'neg' : ''}>{(util * 100).toFixed(1)}%</span>}
        />
        <Card title="Free Capital" value={`$${fmt(dash.margin?.free_capital)}`} />
        <Card title="Max Drawdown" value={`$${fmt(dash.max_drawdown?.max_drawdown)}`} />
        <Card title="Trades Today" value={dash.daily_risk?.trades_today ?? 0} />
      </div>

      <h2 style={{ marginTop: 24 }}>Exposure by Symbol</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Symbol</th><th>Long</th><th>Short</th><th>Gross</th><th>Net</th></tr>
          </thead>
          <tbody>
            {(dash.exposure_by_symbol || []).length === 0 ? (
              <tr><td colSpan={5} className="empty">No exposure</td></tr>
            ) : dash.exposure_by_symbol.map((e: any) => (
              <tr key={e.symbol}>
                <td className="mono">{e.symbol}</td>
                <td>${fmt(e.long_value)}</td>
                <td>${fmt(e.short_value)}</td>
                <td>${fmt(e.gross_value)}</td>
                <td className={e.net_value >= 0 ? 'pos' : 'neg'}>${fmt(e.net_value)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Exposure by Broker</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Broker</th><th>Gross</th><th>Net</th><th>Positions</th></tr>
          </thead>
          <tbody>
            {(dash.exposure_by_broker || []).length === 0 ? (
              <tr><td colSpan={4} className="empty">No broker exposure</td></tr>
            ) : dash.exposure_by_broker.map((b: any) => (
              <tr key={b.broker}>
                <td>{b.broker}</td>
                <td>${fmt(b.gross_value)}</td>
                <td className={b.net_value >= 0 ? 'pos' : 'neg'}>${fmt(b.net_value)}</td>
                <td>{b.positions}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Position Sizing</h2>
      <div className="table-wrap">
        <table className="table">
          <thead>
            <tr><th>Symbol</th><th>Quantity</th><th>Value</th><th>% Capital</th></tr>
          </thead>
          <tbody>
            {(dash.position_sizing || []).length === 0 ? (
              <tr><td colSpan={4} className="empty">No positions</td></tr>
            ) : dash.position_sizing.map((p: any) => (
              <tr key={p.symbol}>
                <td className="mono">{p.symbol}</td>
                <td>{p.quantity}</td>
                <td>${fmt(p.value)}</td>
                <td>{(p.capital_pct * 100).toFixed(2)}%</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
