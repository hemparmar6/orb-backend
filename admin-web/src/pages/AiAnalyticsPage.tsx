import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

export default function AiAnalyticsPage() {
  const [snap, setSnap] = useState<any>(null);
  const [wp, setWp] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.aiPortfolio(), api.aiWinProbability()])
      .then(([s, w]) => { setSnap(s); setWp(w); })
      .catch((e) => setErr(String(e?.message || e)));
  }, []);

  if (err) return <div style={{ padding: 24, color: 'crimson' }}>{err}</div>;
  if (!snap) return <div style={{ padding: 24 }}>Loading…</div>;

  const m = snap.metrics;
  const curve = snap.equity_curve as Array<{ t: string; equity: number }>;

  return (
    <div style={{ padding: 24 }}>
      <h1>AI Analytics</h1>
      <p style={{ color: '#888', fontSize: 13 }}>
        Generated {new Date(snap.generated_at).toLocaleString()}
      </p>
      <div className="grid" data-testid="ai-analytics-grid">
        <Card title="Win rate" value={`${(m.win_rate * 100).toFixed(1)}%`} />
        <Card title="Profit factor" value={m.profit_factor.toFixed(2)} />
        <Card title="Expected value" value={m.expected_value.toFixed(2)} />
        <Card title="Sharpe" value={m.sharpe.toFixed(2)} />
        <Card title="Sortino" value={m.sortino.toFixed(2)} />
        <Card title="Max drawdown" value={`${m.max_drawdown_pct.toFixed(2)}%`} />
        <Card title="Total trades" value={m.total_trades} />
        <Card title="Total P&L" value={m.total_pnl.toFixed(2)} />
        <Card
          title={`Win prob (last ${wp?.last_n ?? 100})`}
          value={wp ? `${(wp.win_probability * 100).toFixed(1)}%` : '—'}
        />
      </div>

      <div style={{ marginTop: 24 }}>
        <h2 style={{ fontSize: 16 }}>Equity curve</h2>
        <EquityChart data={curve} />
      </div>
    </div>
  );
}

function EquityChart({ data }: { data: Array<{ t: string; equity: number }> }) {
  if (!data?.length) return <div style={{ color: '#888' }}>No closed trades yet.</div>;
  const min = Math.min(0, ...data.map((d) => d.equity));
  const max = Math.max(1, ...data.map((d) => d.equity));
  const range = max - min || 1;
  const pts = data
    .map((d, i) => `${(i / Math.max(1, data.length - 1)) * 100},${100 - ((d.equity - min) / range) * 100}`)
    .join(' ');
  return (
    <svg
      viewBox="0 0 100 100" preserveAspectRatio="none"
      style={{ width: '100%', height: 220, background: '#0f1626', borderRadius: 8 }}
      data-testid="ai-equity-chart"
    >
      <polyline points={pts} fill="none" stroke="#4C8BF5" strokeWidth={0.6} />
    </svg>
  );
}
