import { useEffect, useState } from 'react';
import { api } from '../api';
import { Badge } from '../ui';

type Review = {
  id: string; trade_id: string; trade_quality_score: number | null;
  entry_quality: number | null; exit_quality: number | null;
  risk_management_score: number | null; rule_compliance: number | null;
  provider: string; model: string; source: string; created_at: string;
};

export default function StrategyPerformancePage() {
  const [rows, setRows] = useState<Review[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.aiListReviews(100).then(setRows).finally(() => setLoading(false));
  }, []);

  const avg = (k: keyof Review) => {
    const nums = rows.map((r) => (typeof r[k] === 'number' ? Number(r[k]) : null)).filter(Boolean) as number[];
    if (!nums.length) return '—';
    return (nums.reduce((s, v) => s + v, 0) / nums.length).toFixed(1);
  };

  return (
    <div style={{ padding: 24 }}>
      <h1>Strategy performance</h1>
      <p style={{ color: '#888', fontSize: 13 }}>
        Aggregate quality of AI reviews across the last 100 completed trades.
      </p>
      <div className="grid" data-testid="strategy-perf-grid">
        <div className="card"><div className="card-title">Avg quality</div><div className="card-value">{avg('trade_quality_score')}</div></div>
        <div className="card"><div className="card-title">Avg entry</div><div className="card-value">{avg('entry_quality')}</div></div>
        <div className="card"><div className="card-title">Avg exit</div><div className="card-value">{avg('exit_quality')}</div></div>
        <div className="card"><div className="card-title">Avg risk</div><div className="card-value">{avg('risk_management_score')}</div></div>
        <div className="card"><div className="card-title">Rule compliance</div><div className="card-value">{avg('rule_compliance')}</div></div>
      </div>

      {loading ? <div>Loading…</div> : (
        <table className="table" style={{ marginTop: 24 }}>
          <thead>
            <tr>
              <th>Trade</th><th>Quality</th><th>Entry</th><th>Exit</th>
              <th>Risk</th><th>Rules</th><th>Source</th><th>When</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{r.trade_id.slice(0, 8)}…</td>
                <td>{fmt(r.trade_quality_score)}</td>
                <td>{fmt(r.entry_quality)}</td>
                <td>{fmt(r.exit_quality)}</td>
                <td>{fmt(r.risk_management_score)}</td>
                <td>{fmt(r.rule_compliance)}</td>
                <td>
                  <Badge kind={r.source === 'primary' ? 'ok' : 'warn'}>
                    {r.source}
                  </Badge>
                </td>
                <td>{new Date(r.created_at).toLocaleString()}</td>
              </tr>
            ))}
            {!rows.length && <tr><td colSpan={8} style={{ color: '#888' }}>No AI reviews yet.</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}

const fmt = (v: number | null) => (v == null ? '—' : Math.round(v));
