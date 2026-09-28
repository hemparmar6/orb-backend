import { useEffect, useState } from 'react';
import { api } from '../api';
import { Badge, fmtDate } from '../ui';

const JOBS = [
  { id: 'daily_portfolio_snapshot', label: 'Daily Portfolio Snapshot' },
  { id: 'weekly_pnl_digest', label: 'Weekly P&L Digest' },
  { id: 'notification_retry', label: 'Notification Retry' },
];

export default function SchedulerPage() {
  const [status, setStatus] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastRun, setLastRun] = useState<Record<string, any>>({});

  const load = () => {
    api.schedulerStatus().then(setStatus).catch((e) => setErr(String(e?.message || e)));
  };
  useEffect(load, []);

  const trigger = async (jobId: string) => {
    setBusy(jobId);
    try {
      const r = await api.schedulerTrigger(jobId);
      setLastRun((cur) => ({ ...cur, [jobId]: r }));
    } catch (e: any) {
      alert(`Trigger failed: ${e?.message || e}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 16 }}>
        <Badge kind={status?.enabled ? 'ok' : 'warn'}>
          {status?.enabled ? 'ENABLED' : 'DISABLED (SCHEDULER_ENABLED=false)'}
        </Badge>
        <button className="btn btn-sm" onClick={load}>Refresh</button>
      </div>

      {err ? <div className="empty">Error: {err}</div> : null}

      <h2>Registered jobs</h2>
      <div className="table-wrap" data-testid="scheduled-jobs-table">
        <table className="table">
          <thead>
            <tr><th>Job ID</th><th>Trigger</th><th>Next Run</th></tr>
          </thead>
          <tbody>
            {(status?.jobs || []).length === 0 ? (
              <tr><td colSpan={3} className="empty">
                Scheduler is disabled or no jobs are registered.
                Set <code>SCHEDULER_ENABLED=true</code> to activate.
              </td></tr>
            ) : status.jobs.map((j: any) => (
              <tr key={j.id}>
                <td className="mono">{j.id}</td>
                <td className="mono" style={{ fontSize: 12 }}>{j.trigger}</td>
                <td className="mono">{fmtDate(j.next_run_time)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Trigger on demand</h2>
      <div className="card-row">
        {JOBS.map((j) => (
          <div key={j.id} className="card" data-testid={`trigger-card-${j.id}`}>
            <div className="card-title">{j.label}</div>
            <div className="muted" style={{ fontSize: 12 }}>{j.id}</div>
            <div style={{ marginTop: 12 }}>
              <button
                className="btn btn-sm"
                data-testid={`trigger-${j.id}`}
                disabled={busy === j.id}
                onClick={() => trigger(j.id)}
              >
                {busy === j.id ? 'Running…' : 'Run now'}
              </button>
            </div>
            {lastRun[j.id] ? (
              <pre style={{
                marginTop: 12,
                fontSize: 11,
                background: '#0f1220',
                padding: 8,
                borderRadius: 6,
                overflow: 'auto',
                maxHeight: 120,
              }}>
                {JSON.stringify(lastRun[j.id], null, 2)}
              </pre>
            ) : null}
          </div>
        ))}
      </div>
    </div>
  );
}
