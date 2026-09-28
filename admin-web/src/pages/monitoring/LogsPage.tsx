import { useEffect, useState } from 'react';
import { api } from '../../api';

const CATEGORIES = ['application', 'error', 'security', 'ai', 'audit', 'trade', 'access'] as const;
const LEVELS = ['', 'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'] as const;

export default function LogsPage() {
  const [category, setCategory] = useState<string>('application');
  const [level, setLevel] = useState<string>('');
  const [contains, setContains] = useState<string>('');
  const [limit, setLimit] = useState<number>(200);
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const load = async () => {
    setLoading(true);
    setErr(null);
    try {
      const v = await api.monitoringLogs({ category, level, contains, limit });
      setData(v);
    } catch (e: any) {
      setErr(e.message || 'Failed');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [category]);

  return (
    <div data-testid="logs-page">
      <div className="toolbar">
        <select data-testid="logs-category-select" value={category} onChange={e => setCategory(e.target.value)}>
          {CATEGORIES.map(c => <option key={c} value={c}>{c} ({data?.stats?.[c] ?? 0})</option>)}
        </select>
        <select data-testid="logs-level-select" value={level} onChange={e => setLevel(e.target.value)}>
          {LEVELS.map(l => <option key={l} value={l}>{l || '(any level)'}</option>)}
        </select>
        <input
          data-testid="logs-search-input"
          placeholder="Contains…"
          value={contains}
          onChange={e => setContains(e.target.value)}
          style={{ minWidth: 200 }}
        />
        <select data-testid="logs-limit-select" value={limit} onChange={e => setLimit(Number(e.target.value))}>
          {[100, 200, 500, 1000].map(n => <option key={n} value={n}>{n} rows</option>)}
        </select>
        <button className="btn btn-sm" data-testid="logs-refresh-button" onClick={load} disabled={loading}>
          {loading ? '…' : '↻ Search'}
        </button>
        <div className="spacer" />
        <div className="dim">source: in-memory ring buffer (per-process)</div>
      </div>

      {err && <div className="card" style={{ color: 'var(--red)' }} data-testid="logs-error">{err}</div>}

      <div className="card" data-testid="logs-table">
        <table className="tbl">
          <thead>
            <tr>
              <th style={{ width: 180 }}>Timestamp</th>
              <th style={{ width: 80 }}>Level</th>
              <th style={{ width: 160 }}>Logger</th>
              <th>Message</th>
              <th style={{ width: 240 }}>Request ID</th>
            </tr>
          </thead>
          <tbody>
            {(data?.entries || []).map((e: any, i: number) => (
              <tr key={i}>
                <td className="mono" style={{ fontSize: 11 }}>{e.timestamp?.slice(0, 19).replace('T', ' ')}</td>
                <td><span className={`badge lvl-${(e.level || '').toLowerCase()}`}>{e.level}</span></td>
                <td className="mono" style={{ fontSize: 12 }}>{e.logger}</td>
                <td className="mono" style={{ fontSize: 12 }}>{e.message}</td>
                <td className="mono" style={{ fontSize: 11, opacity: 0.7 }}>{e.request_id}</td>
              </tr>
            ))}
            {(!data?.entries || data.entries.length === 0) && (
              <tr><td colSpan={5} className="dim ta-c">No log entries.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
