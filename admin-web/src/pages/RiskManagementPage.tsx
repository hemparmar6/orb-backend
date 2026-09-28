import { useEffect, useState } from 'react';
import { api } from '../api';
import { Card } from '../ui';

/**
 * Milestone 9 — Risk Management admin console.
 *
 * Sections
 * --------
 * 1. Overview (aggregate breach counters + configuration toggles)
 * 2. Breach log (filterable)
 * 3. Per-user limits editor
 */
export default function RiskManagementPage() {
  const [overview, setOverview] = useState<any>(null);
  const [breaches, setBreaches] = useState<any[]>([]);
  const [filter, setFilter] = useState({ severity: '', event_type: '', unresolved_only: false });
  const [err, setErr] = useState<string | null>(null);
  const [userId, setUserId] = useState('');
  const [userLimits, setUserLimits] = useState<any>(null);

  const load = async () => {
    try {
      const [ov, br] = await Promise.all([
        api.rmAdminOverview(1440),
        api.rmAdminListBreaches({
          limit: 100,
          severity: filter.severity || undefined,
          event_type: filter.event_type || undefined,
          unresolved_only: filter.unresolved_only || undefined,
        }),
      ]);
      setOverview(ov);
      setBreaches(br);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  useEffect(() => { load(); }, [filter.severity, filter.event_type, filter.unresolved_only]);

  const loadUser = async () => {
    if (!userId.trim()) return;
    try {
      const row = await api.rmAdminGetUserLimits(userId.trim());
      setUserLimits(row);
    } catch (e: any) {
      setErr(String(e?.message || e));
    }
  };

  const saveUser = async () => {
    if (!userLimits) return;
    const payload: any = { ...userLimits };
    delete payload.id;
    delete payload.user_id;
    delete payload.created_at;
    delete payload.updated_at;
    delete payload.extra;
    const row = await api.rmAdminSetUserLimits(userId.trim(), payload);
    setUserLimits(row);
    alert('Saved');
  };

  const resolve = async (id: string) => {
    await api.rmAdminResolveBreach(id);
    load();
  };

  const prune = async () => {
    if (!confirm('Prune risk breaches older than 90 days?')) return;
    const r = await api.rmAdminPrune(90);
    alert(`Removed ${r.removed} rows`);
    load();
  };

  if (err) return <div className="empty" data-testid="rm-error">Error: {err}</div>;
  if (!overview) return <div className="empty">Loading…</div>;

  return (
    <div data-testid="risk-management-page">
      <div className="card-row">
        <Card title="Breaches (24h)" value={overview.total_breaches} />
        <Card title="Users w/ live off" value={overview.users_with_live_disabled} />
        <Card title="Users forced to paper" value={overview.users_forced_to_paper} />
      </div>

      <div style={{ marginTop: 24, display: 'flex', gap: 12, alignItems: 'end', flexWrap: 'wrap' }}>
        <div>
          <div className="dim">Severity</div>
          <select
            data-testid="rm-filter-severity"
            value={filter.severity}
            onChange={e => setFilter({ ...filter, severity: e.target.value })}
          >
            <option value="">All</option>
            <option value="info">Info</option>
            <option value="warning">Warning</option>
            <option value="error">Error</option>
            <option value="critical">Critical</option>
          </select>
        </div>
        <div>
          <div className="dim">Event type</div>
          <select
            data-testid="rm-filter-event"
            value={filter.event_type}
            onChange={e => setFilter({ ...filter, event_type: e.target.value })}
          >
            <option value="">All</option>
            <option value="daily_loss_limit">daily_loss_limit</option>
            <option value="daily_profit_target">daily_profit_target</option>
            <option value="max_trades_per_day">max_trades_per_day</option>
            <option value="max_consecutive_losses">max_consecutive_losses</option>
            <option value="max_capital_allocation">max_capital_allocation</option>
            <option value="max_position_size">max_position_size</option>
            <option value="max_open_positions">max_open_positions</option>
            <option value="max_exposure_per_symbol">max_exposure_per_symbol</option>
            <option value="trading_session_hours">trading_session_hours</option>
            <option value="live_trading_disabled">live_trading_disabled</option>
            <option value="paper_mode_forced">paper_mode_forced</option>
          </select>
        </div>
        <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
          <input
            type="checkbox"
            data-testid="rm-filter-unresolved"
            checked={filter.unresolved_only}
            onChange={e => setFilter({ ...filter, unresolved_only: e.target.checked })}
          />
          <span>Unresolved only</span>
        </label>
        <button data-testid="rm-prune-btn" onClick={prune}>Prune 90d+</button>
      </div>

      <h2 style={{ marginTop: 24 }}>Recent Breaches</h2>
      <div className="table-wrap">
        <table className="table" data-testid="rm-breaches-table">
          <thead>
            <tr>
              <th>Time</th><th>User</th><th>Type</th><th>Sev</th>
              <th>Action</th><th>Symbol</th><th>Reason</th><th>Resolved</th><th></th>
            </tr>
          </thead>
          <tbody>
            {breaches.length === 0 ? (
              <tr><td colSpan={9} className="empty">No breaches</td></tr>
            ) : breaches.map((b) => (
              <tr key={b.id}>
                <td className="mono">{b.created_at?.slice(0, 19)?.replace('T', ' ')}</td>
                <td className="mono">{b.user_id?.slice(0, 8)}</td>
                <td>{b.event_type}</td>
                <td className={b.severity === 'critical' ? 'neg' : ''}>{b.severity}</td>
                <td>{b.action_taken}</td>
                <td>{b.symbol || '-'}</td>
                <td style={{ maxWidth: 300, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                  {b.reason}
                </td>
                <td>{b.resolved_at ? '✓' : ''}</td>
                <td>
                  {!b.resolved_at && (
                    <button
                      data-testid={`rm-resolve-${b.id}`}
                      onClick={() => resolve(b.id)}
                    >Resolve</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2 style={{ marginTop: 24 }}>Per-User Limits</h2>
      <div style={{ display: 'flex', gap: 8, alignItems: 'end' }}>
        <input
          data-testid="rm-user-id-input"
          placeholder="User ID"
          value={userId}
          onChange={e => setUserId(e.target.value)}
          style={{ minWidth: 300 }}
        />
        <button data-testid="rm-load-user-btn" onClick={loadUser}>Load</button>
      </div>
      {userLimits && (
        <div style={{ marginTop: 16, maxWidth: 720 }}>
          <UserLimitEditor row={userLimits} onChange={setUserLimits} />
          <button data-testid="rm-save-user-btn" onClick={saveUser} style={{ marginTop: 12 }}>
            Save changes
          </button>
        </div>
      )}
    </div>
  );
}

function UserLimitEditor({ row, onChange }: { row: any; onChange: (v: any) => void }) {
  const fields: Array<[keyof any, string, string]> = [
    ['daily_loss_limit', 'Daily loss limit', 'number'],
    ['daily_profit_target', 'Daily profit target', 'number'],
    ['max_trades_per_day', 'Max trades / day', 'number'],
    ['max_consecutive_losses', 'Max consecutive losses', 'number'],
    ['max_capital_allocation', 'Max capital allocation', 'number'],
    ['max_position_size', 'Max position size', 'number'],
    ['max_open_positions', 'Max open positions', 'number'],
    ['max_exposure_per_symbol', 'Max exposure / symbol', 'number'],
    ['trading_session_start', 'Session start (HH:MM)', 'text'],
    ['trading_session_end', 'Session end (HH:MM)', 'text'],
    ['trading_session_timezone', 'Timezone (IANA)', 'text'],
  ];
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 8 }}>
      {fields.map(([k, label, type]) => (
        <div key={String(k)}>
          <div className="dim">{label}</div>
          <input
            data-testid={`rm-input-${String(k)}`}
            type={type}
            value={row[k] ?? ''}
            onChange={e => onChange({ ...row, [k]: e.target.value === '' ? null : e.target.value })}
            style={{ width: '100%' }}
          />
        </div>
      ))}
      <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input
          type="checkbox"
          data-testid="rm-input-live-enabled"
          checked={!!row.live_trading_enabled}
          onChange={e => onChange({ ...row, live_trading_enabled: e.target.checked })}
        />
        <span>Live trading enabled</span>
      </label>
      <label style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
        <input
          type="checkbox"
          data-testid="rm-input-force-paper"
          checked={!!row.force_paper_mode}
          onChange={e => onChange({ ...row, force_paper_mode: e.target.checked })}
        />
        <span>Force paper mode</span>
      </label>
    </div>
  );
}
