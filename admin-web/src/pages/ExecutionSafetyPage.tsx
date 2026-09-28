import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

/**
 * Milestone 8/9 — Execution Safety admin dashboard.
 * Combines settings editor, kill switch, dashboard aggregates, and audit log.
 */
export default function ExecutionSafetyPage() {
  const [settings, setSettings] = useState<any>(null);
  const [dashboard, setDashboard] = useState<any>(null);
  const [events, setEvents] = useState<any[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [ksReason, setKsReason] = useState('');

  const load = async () => {
    try {
      const [s, d, e] = await Promise.all([
        api.esSettings(),
        api.esDashboard(60),
        api.esEvents({ limit: 50 }),
      ]);
      setSettings(s); setDashboard(d); setEvents(e);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };
  useEffect(() => { load(); }, []);

  const saveSettings = async () => {
    if (!settings) return;
    const payload: any = { ...settings, reason: 'admin panel edit' };
    delete payload.id; delete payload.created_at; delete payload.updated_at;
    delete payload.kill_switch_activated_at;
    delete payload.kill_switch_activated_by;
    delete payload.kill_switch_active;
    delete payload.kill_switch_reason;
    delete payload.extra;
    const s = await api.esUpdateSettings(payload);
    setSettings(s);
    alert('Settings saved');
  };

  const toggleKS = async () => {
    if (!settings) return;
    const want = !settings.kill_switch_active;
    if (!confirm(`${want ? 'ACTIVATE' : 'DEACTIVATE'} the global kill switch?`)) return;
    const reason = ksReason.trim() || (want ? 'admin_activate' : 'admin_deactivate');
    const s = await api.esKillSwitch(want, reason);
    setSettings(s);
  };

  const prune = async () => {
    if (!confirm('Prune execution-safety events older than retention?')) return;
    const r = await api.esPrune();
    alert(`Removed ${r.removed}`);
    load();
  };

  if (err) return <div className="empty" data-testid="es-error">Error: {err}</div>;
  if (!settings) return <div className="empty">Loading…</div>;

  return (
    <div data-testid="execution-safety-page">
      <div className="card-row">
        <Card title="Queue Size" value={dashboard?.queue_size ?? 0} />
        <Card title="Events (1h)" value={dashboard?.total_events ?? 0} />
        <Card
          title="Kill Switch"
          value={
            <span className={settings.kill_switch_active ? 'neg' : 'pos'}>
              {settings.kill_switch_active ? 'ACTIVE' : 'off'}
            </span>
          }
        />
        <Card title="Auto-pause" value={settings.auto_pause_enabled ? 'on' : 'off'} />
      </div>

      <h2 style={{ marginTop: 24 }}>Global Kill Switch</h2>
      <div style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
        <input
          data-testid="es-ks-reason"
          placeholder="Reason (audited)"
          value={ksReason}
          onChange={e => setKsReason(e.target.value)}
          style={{ minWidth: 300 }}
        />
        <button
          data-testid="es-ks-toggle"
          onClick={toggleKS}
          className={settings.kill_switch_active ? 'neg' : ''}
        >
          {settings.kill_switch_active ? 'Deactivate' : 'Activate'}
        </button>
      </div>

      <h2 style={{ marginTop: 24 }}>Limits Configuration</h2>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 8, maxWidth: 900 }}>
        {[
          ['trades_per_second', 'Trades / second'],
          ['orders_per_minute', 'Orders / minute'],
          ['orders_per_hour', 'Orders / hour'],
          ['global_orders_per_second', 'Global orders / second'],
          ['global_orders_per_minute', 'Global orders / minute'],
          ['duplicate_window_seconds', 'Duplicate window (s)'],
          ['queue_max_size', 'Queue max size'],
          ['queue_timeout_seconds', 'Queue timeout (s)'],
          ['auto_pause_violations', 'Auto-pause violations'],
          ['auto_pause_window_seconds', 'Auto-pause window (s)'],
          ['event_retention_days', 'Retention (days)'],
        ].map(([k, label]) => (
          <div key={k}>
            <div className="dim">{label}</div>
            <input
              data-testid={`es-input-${k}`}
              type="number"
              value={settings[k] ?? ''}
              onChange={e => setSettings({ ...settings, [k]: Number(e.target.value) })}
              style={{ width: '100%' }}
            />
          </div>
        ))}
      </div>
      <div style={{ marginTop: 12, display: 'flex', gap: 8 }}>
        <button data-testid="es-save-btn" onClick={saveSettings}>Save settings</button>
        <button data-testid="es-prune-btn" onClick={prune}>Prune old events</button>
      </div>

      <h2 style={{ marginTop: 24 }}>Recent Events</h2>
      <div className="table-wrap">
        <table className="table" data-testid="es-events-table">
          <thead>
            <tr><th>Time</th><th>User</th><th>Type</th><th>Action</th><th>Symbol</th><th>Reason</th></tr>
          </thead>
          <tbody>
            {events.length === 0 ? (
              <tr><td colSpan={6} className="empty">No events</td></tr>
            ) : events.map((e: any) => (
              <tr key={e.id}>
                <td className="mono">{e.created_at?.slice(0, 19)?.replace('T', ' ')}</td>
                <td className="mono">{e.user_id?.slice(0, 8) || '-'}</td>
                <td>{e.limit_type}</td>
                <td>{e.action}</td>
                <td>{e.symbol || '-'}</td>
                <td style={{ maxWidth: 320, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {e.reason}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
