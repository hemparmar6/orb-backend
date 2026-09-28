import { useEffect, useState } from 'react';
import { api } from '../../api';
import { Card } from '../../ui';

export default function SecurityPage() {
  const [summary, setSummary] = useState<any>(null);
  const [activity, setActivity] = useState<any[] | null>(null);
  const [rl, setRl] = useState<any[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);
  const [window_, setWindow] = useState<number>(24);
  const [showFailedOnly, setShowFailedOnly] = useState(false);

  const load = async () => {
    setErr(null);
    try {
      const [s, a, r] = await Promise.all([
        api.monitoringSecuritySummary(window_),
        api.monitoringLoginActivity({ success: showFailedOnly ? false : undefined, limit: 100 }),
        api.monitoringRateLimitEvents(100),
      ]);
      setSummary(s); setActivity(a); setRl(r);
    } catch (e: any) {
      setErr(e.message || 'Failed');
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tick, window_, showFailedOnly]);

  return (
    <div data-testid="security-page">
      <div className="toolbar">
        <div className="dim">Security overview · <code>/api/v1/monitoring/security/*</code></div>
        <div className="spacer" />
        <select data-testid="sec-window-select" value={window_} onChange={e => setWindow(Number(e.target.value))}>
          <option value={1}>Last 1h</option>
          <option value={24}>Last 24h</option>
          <option value={72}>Last 3d</option>
          <option value={168}>Last 7d</option>
        </select>
        <label className="dim" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
          <input data-testid="sec-failed-only" type="checkbox" checked={showFailedOnly} onChange={e => setShowFailedOnly(e.target.checked)} />
          failed only
        </label>
        <button className="btn btn-sm" data-testid="sec-refresh-button" onClick={() => setTick(t => t + 1)}>↻ Refresh</button>
      </div>

      {err && <div className="card" style={{ color: 'var(--red)' }} data-testid="sec-error">{err}</div>}

      <div className="grid grid-4">
        <Card testId="sec-login-ok" title="Login Success" value={summary?.login_success ?? '—'} sub={`Last ${summary?.window_hours ?? window_}h`} />
        <Card testId="sec-login-fail" title="Login Failed" value={summary?.login_failed ?? '—'} sub="Persistent (audit)" />
        <Card testId="sec-ratelimit" title="Rate-Limit Events" value={summary?.rate_limit_events ?? '—'} sub="Persistent (audit)" />
        <Card testId="sec-config"
              title="Rate-Limit / HSTS / CSP"
              value={`${summary?.config?.rate_limit_per_minute ?? '—'} rpm`}
              sub={`hsts:${String(summary?.config?.hsts_enabled)} · csp:${String(summary?.config?.csp_enabled)}`} />
      </div>

      <div className="section-title">Recent login activity</div>
      <div className="card" data-testid="sec-login-table">
        <table className="tbl">
          <thead><tr><th>Time</th><th>Email</th><th>Status</th><th>Reason</th><th>IP</th><th>Agent</th></tr></thead>
          <tbody>
            {(activity || []).map(a => (
              <tr key={a.id}>
                <td className="mono" style={{ fontSize: 11 }}>{String(a.created_at).slice(0, 19).replace('T', ' ')}</td>
                <td className="mono">{a.email}</td>
                <td>{a.success ? <span className="badge lvl-info">OK</span> : <span className="badge lvl-error">FAILED</span>}</td>
                <td className="dim">{a.reason || '—'}</td>
                <td className="mono">{a.ip_address || '—'}</td>
                <td className="mono" style={{ fontSize: 11, opacity: 0.7 }}>{(a.user_agent || '').slice(0, 60)}</td>
              </tr>
            ))}
            {(!activity || activity.length === 0) && <tr><td colSpan={6} className="dim ta-c">No login events.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="section-title">Recent rate-limit events</div>
      <div className="card" data-testid="sec-rl-table">
        <table className="tbl">
          <thead><tr><th>Time</th><th>Key</th><th>Method</th><th>Path</th><th>IP</th><th className="ta-r">Retry after</th></tr></thead>
          <tbody>
            {(rl || []).map(e => (
              <tr key={e.id}>
                <td className="mono" style={{ fontSize: 11 }}>{String(e.created_at).slice(0, 19).replace('T', ' ')}</td>
                <td className="mono">{e.key}</td>
                <td>{e.method}</td>
                <td className="mono">{e.path}</td>
                <td className="mono">{e.ip_address || '—'}</td>
                <td className="ta-r">{e.retry_after_seconds ?? '—'}s</td>
              </tr>
            ))}
            {(!rl || rl.length === 0) && <tr><td colSpan={6} className="dim ta-c">No rate-limit events.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
