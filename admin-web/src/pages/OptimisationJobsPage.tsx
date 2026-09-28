import { useEffect, useState } from 'react';
import { api } from '../api';
import { Badge, statusBadge } from '../ui';

type Job = {
  id: string; kind: string; status: string; strategy_id: string;
  started_at: string | null; finished_at: string | null;
  error: string | null; created_at: string;
};

export default function OptimisationJobsPage() {
  const [jobs, setJobs] = useState<Job[]>([]);
  const [loading, setLoading] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [results, setResults] = useState<Record<string, any[]>>({});

  const load = () => {
    setLoading(true);
    api.aiListOptimJobs().then(setJobs).finally(() => setLoading(false));
  };
  useEffect(load, []);

  const toggle = async (jobId: string) => {
    if (expanded === jobId) { setExpanded(null); return; }
    setExpanded(jobId);
    if (!results[jobId]) {
      const r = await api.aiOptimResults(jobId, 10).catch(() => []);
      setResults((s) => ({ ...s, [jobId]: r }));
    }
  };

  return (
    <div style={{ padding: 24 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1>Optimisation jobs</h1>
        <button className="btn" onClick={load}>Refresh</button>
      </div>
      {loading ? <div>Loading…</div> : (
        <table className="table" data-testid="optim-jobs-table">
          <thead>
            <tr><th>ID</th><th>Kind</th><th>Strategy</th><th>Status</th><th>Started</th><th>Finished</th></tr>
          </thead>
          <tbody>
            {jobs.map((j) => (
              <>
                <tr key={j.id} onClick={() => toggle(j.id)} style={{ cursor: 'pointer' }}>
                  <td>{j.id.slice(0, 8)}…</td>
                  <td>{j.kind}</td>
                  <td>{j.strategy_id.slice(0, 8)}…</td>
                  <td>{statusBadge(j.status)}</td>
                  <td>{j.started_at ? new Date(j.started_at).toLocaleString() : '—'}</td>
                  <td>{j.finished_at ? new Date(j.finished_at).toLocaleString() : '—'}</td>
                </tr>
                {expanded === j.id && (
                  <tr key={`${j.id}-details`}>
                    <td colSpan={6} style={{ background: '#0f1626' }}>
                      <TopResults rows={results[j.id] || []} />
                      {j.error && <div style={{ color: 'crimson' }}>{j.error}</div>}
                    </td>
                  </tr>
                )}
              </>
            ))}
            {!jobs.length && <tr><td colSpan={6} style={{ color: '#888' }}>No jobs yet.</td></tr>}
          </tbody>
        </table>
      )}
    </div>
  );
}

function TopResults({ rows }: { rows: any[] }) {
  if (!rows.length) return <div style={{ color: '#888', padding: 12 }}>No results yet.</div>;
  return (
    <table className="table" style={{ margin: 8 }}>
      <thead><tr><th>#</th><th>Params</th><th>Sharpe</th><th>Net P/L</th><th>Best?</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.id}>
            <td>{r.rank}</td>
            <td><code style={{ fontSize: 11 }}>{JSON.stringify(r.params)}</code></td>
            <td>{(r.metrics?.sharpe ?? 0).toFixed(2)}</td>
            <td>{(r.metrics?.net_profit ?? 0).toFixed(2)}</td>
            <td>{r.is_best ? <Badge kind="ok">best</Badge> : ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
