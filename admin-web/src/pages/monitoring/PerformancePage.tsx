import { useEffect, useState } from 'react';
import { api } from '../../api';
import { Card } from '../../ui';

export default function PerformancePage() {
  const [p, setP] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let cancel = false;
    api.monitoringPerformance()
      .then(v => !cancel && setP(v))
      .catch(e => !cancel && setErr(e.message || 'Failed'));
    return () => { cancel = true; };
  }, [tick]);

  useEffect(() => {
    const t = setInterval(() => setTick(x => x + 1), 15_000);
    return () => clearInterval(t);
  }, []);

  const worst = (p?.endpoints || []).slice().sort((a: any, b: any) => (b.latency_ms?.p95 ?? 0) - (a.latency_ms?.p95 ?? 0)).slice(0, 10);
  const busiest = (p?.endpoints || []).slice(0, 10);

  return (
    <div data-testid="performance-page">
      <div className="toolbar">
        <div className="dim">
          Per-endpoint latency · slow threshold {p?.slow_request_threshold_ms ?? '—'} ms · <code>/api/v1/monitoring/performance</code>
        </div>
        <div className="spacer" />
        <button className="btn btn-sm" data-testid="perf-refresh-button" onClick={() => setTick(t => t + 1)}>↻ Refresh</button>
      </div>

      {err && <div className="card" style={{ color: 'var(--red)' }} data-testid="perf-error">{err}</div>}

      <div className="grid grid-4">
        <Card testId="p-uptime" title="Uptime" value={`${p?.uptime_s ?? 0}s`} sub="Process uptime" />
        <Card testId="p-total" title="Requests" value={p?.total_requests ?? '—'} sub="Since process start" />
        <Card testId="p-err" title="Error rate" value={`${((p?.error_rate ?? 0) * 100).toFixed(2)}%`} sub="5xx / total" />
        <Card testId="p-buckets" title="4xx / 5xx" value={`${p?.status_buckets?.['4xx'] ?? 0} / ${p?.status_buckets?.['5xx'] ?? 0}`} sub="Status buckets" />
      </div>

      <div className="section-title">Slowest endpoints (p95)</div>
      <div className="card" data-testid="p-slowest">
        <table className="tbl">
          <thead><tr><th>Endpoint</th><th className="ta-r">Requests</th><th className="ta-r">Avg</th><th className="ta-r">p50</th><th className="ta-r">p95</th><th className="ta-r">p99</th></tr></thead>
          <tbody>
            {worst.map((e: any) => (
              <tr key={e.endpoint}>
                <td className="mono">{e.endpoint}</td>
                <td className="ta-r">{e.count}</td>
                <td className="ta-r">{e.latency_ms?.avg}</td>
                <td className="ta-r">{e.latency_ms?.p50}</td>
                <td className="ta-r"><strong>{e.latency_ms?.p95}</strong></td>
                <td className="ta-r">{e.latency_ms?.p99}</td>
              </tr>
            ))}
            {worst.length === 0 && <tr><td colSpan={6} className="dim ta-c">No traffic recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>

      <div className="section-title">Busiest endpoints</div>
      <div className="card" data-testid="p-busiest">
        <table className="tbl">
          <thead><tr><th>Endpoint</th><th className="ta-r">Requests</th><th className="ta-r">Errors</th><th className="ta-r">Avg ms</th><th className="ta-r">p95</th></tr></thead>
          <tbody>
            {busiest.map((e: any) => (
              <tr key={e.endpoint}>
                <td className="mono">{e.endpoint}</td>
                <td className="ta-r">{e.count}</td>
                <td className="ta-r">{e.errors}</td>
                <td className="ta-r">{e.latency_ms?.avg}</td>
                <td className="ta-r">{e.latency_ms?.p95}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
