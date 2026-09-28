import { useEffect, useState } from 'react';
import { api } from '../../api';
import { Card } from '../../ui';

export default function BackupsPage() {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tick, setTick] = useState(0);

  const load = async () => {
    setErr(null);
    try { setData(await api.monitoringBackups()); }
    catch (e: any) { setErr(e.message || 'Failed'); }
  };
  useEffect(() => { load(); /* eslint-disable-next-line */ }, [tick]);

  const trigger = async () => {
    if (busy) return;
    setBusy(true); setErr(null);
    try {
      await api.monitoringBackupsRun();
      await load();
    } catch (e: any) { setErr(e.message || 'Failed'); }
    finally { setBusy(false); }
  };

  const cfg = data?.config;
  const items: any[] = data?.items || [];
  const last = data?.last_successful;

  const disabled = !cfg?.enabled;

  return (
    <div data-testid="backups-page">
      <div className="toolbar">
        <div className="dim">
          Backup history · <code>/api/v1/monitoring/backups</code>
        </div>
        <div className="spacer" />
        <button
          className="btn btn-sm"
          data-testid="backup-run-button"
          disabled={busy || disabled}
          title={disabled ? 'Set BACKUP_ENABLED=true in .env to enable manual backups' : 'Run backup now'}
          onClick={trigger}
        >
          {busy ? 'Running…' : '⬇ Run backup now'}
        </button>
        <button className="btn btn-sm" data-testid="backup-refresh-button" onClick={() => setTick(t => t + 1)}>↻ Refresh</button>
      </div>

      {err && <div className="card" style={{ color: 'var(--red)' }} data-testid="backup-error">{err}</div>}

      <div className="grid grid-4">
        <Card testId="bk-enabled" title="Enabled" value={String(cfg?.enabled ?? false)} sub={`schedule ${cfg?.schedule_cron ?? '—'}`} />
        <Card testId="bk-s3" title="S3 upload" value={String(cfg?.s3_enabled ?? false)} sub={cfg?.s3_bucket || 'no bucket'} />
        <Card testId="bk-last" title="Last successful"
              value={last ? new Date(last.finished_at || last.created_at).toISOString().slice(0, 19).replace('T', ' ') : '—'}
              sub={last ? `${last.size_bytes ?? '—'} bytes · ${last.kind}` : 'no backups yet'} />
        <Card testId="bk-retention" title="Retention"
              value={`${cfg?.retention_days ?? 0} days`}
              sub={cfg?.local_dir || '—'} />
      </div>

      <div className="section-title">History</div>
      <div className="card" data-testid="bk-history">
        <table className="tbl">
          <thead>
            <tr>
              <th>Created</th>
              <th>Kind</th>
              <th>Status</th>
              <th className="ta-r">Size</th>
              <th className="ta-r">Duration</th>
              <th>S3</th>
              <th>Verified</th>
              <th>File</th>
            </tr>
          </thead>
          <tbody>
            {items.map((b: any) => (
              <tr key={b.id}>
                <td className="mono" style={{ fontSize: 11 }}>{String(b.created_at).slice(0, 19).replace('T', ' ')}</td>
                <td>{b.kind}</td>
                <td>
                  <span className={`badge lvl-${b.status === 'verified' || b.status === 'success' ? 'info' : b.status === 'failed' ? 'error' : 'warning'}`}>
                    {b.status}
                  </span>
                </td>
                <td className="ta-r">{b.size_bytes ?? '—'}</td>
                <td className="ta-r">{b.duration_ms ? `${b.duration_ms} ms` : '—'}</td>
                <td>{b.uploaded_to_s3 ? '✓' : '—'}</td>
                <td>{b.verified ? '✓' : '—'}</td>
                <td className="mono" style={{ fontSize: 11 }}>{b.filename || '—'}</td>
              </tr>
            ))}
            {items.length === 0 && <tr><td colSpan={8} className="dim ta-c">No backups recorded yet.</td></tr>}
          </tbody>
        </table>
      </div>
    </div>
  );
}
