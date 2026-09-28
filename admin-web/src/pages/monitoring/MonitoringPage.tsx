import { useEffect, useState } from 'react';
import { api } from '../../api';
import { Card } from '../../ui';

export default function MonitoringPage() {
  const [m, setM] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancel = false;
    api.monitoringMetrics()
      .then(v => !cancel && setM(v))
      .catch(e => !cancel && setErr(e.message || 'Failed'));
    return () => { cancel = true; };
  }, [tick]);

  useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), 10_000);
    return () => clearInterval(t);
  }, []);

  return (
    <div data-testid="monitoring-page">
      <div className="toolbar">
        <div className="dim">Runtime metrics · refreshes every 10s · <code>/api/v1/monitoring/metrics</code></div>
        <div className="spacer" />
        <button className="btn btn-sm" data-testid="monitoring-refresh-button" onClick={() => setTick(t => t + 1)}>↻ Refresh</button>
      </div>

      {err && <div className="card" style={{ color: 'var(--red)' }} data-testid="monitoring-error">{err}</div>}

      <div className="grid grid-4">
        <Card testId="m-requests" title="Total Requests" value={m?.total_requests ?? '—'} sub={`uptime ${m?.uptime_s ?? 0}s`} />
        <Card testId="m-errors" title="5xx Errors" value={m?.total_errors ?? '—'} sub={`rate ${((m?.error_rate ?? 0) * 100).toFixed(2)}%`} />
        <Card testId="m-ratelimit" title="Rate-Limit Hits" value={m?.rate_limit_hits ?? '—'} sub="Since process start" />
        <Card testId="m-auth" title="Auth (ok / fail)" value={`${m?.auth_success ?? 0} / ${m?.auth_failures ?? 0}`} sub="Login attempts" />
      </div>

      <div className="section-title">Status buckets</div>
      <div className="grid grid-4" data-testid="m-buckets">
        {['2xx', '3xx', '4xx', '5xx'].map(b => (
          <Card key={b} testId={`m-bucket-${b}`} title={b.toUpperCase()} value={m?.status_buckets?.[b] ?? 0} sub={b} />
        ))}
      </div>

      <div className="section-title">Endpoints (top 20 by traffic)</div>
      <div className="card" data-testid="m-endpoints">
        <table className="tbl">
          <thead>
            <tr>
              <th>Endpoint</th>
              <th className="ta-r">Requests</th>
              <th className="ta-r">Errors</th>
              <th className="ta-r">Avg ms</th>
              <th className="ta-r">p50</th>
              <th className="ta-r">p95</th>
              <th className="ta-r">p99</th>
            </tr>
          </thead>
          <tbody>
            {(m?.endpoints || []).slice(0, 20).map((e: any) => (
              <tr key={e.endpoint}>
                <td className="mono">{e.endpoint}</td>
                <td className="ta-r">{e.count}</td>
                <td className="ta-r">{e.errors}</td>
                <td className="ta-r">{e.latency_ms?.avg}</td>
                <td className="ta-r">{e.latency_ms?.p50}</td>
                <td className="ta-r">{e.latency_ms?.p95}</td>
                <td className="ta-r">{e.latency_ms?.p99}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
