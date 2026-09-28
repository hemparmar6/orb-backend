// Admin — Circuit Breaker Configuration + Event history.
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api, ApiError } from '../api';
import { Badge, fmtDate, Toast } from '../ui';

const LEVELS = ['user', 'strategy', 'broker', 'global'] as const;
type Level = typeof LEVELS[number];

const TYPES_BY_LEVEL: Record<Level, string[]> = {
  user: [
    'max_daily_loss', 'max_daily_profit', 'max_running_bots',
    'max_open_positions', 'max_consecutive_losses', 'trading_window',
  ],
  strategy: [
    'max_drawdown', 'strategy_cooldown', 'strategy_auto_pause',
  ],
  broker: [
    'api_failure_threshold', 'disconnect_detection',
    'order_rejection_threshold', 'broker_health_monitoring',
  ],
  global: [
    'global_kill_switch', 'holiday_lock', 'emergency_stop', 'maintenance_mode',
  ],
};

const ACTIONS = ['pause', 'stop', 'kill', 'block_new', 'warn'] as const;

type Config = {
  id: string;
  level: string;
  breaker_type: string;
  enabled: boolean;
  action: string;
  value_num: number | null;
  value_json: any;
  unit: string | null;
  user_id: string | null;
  strategy_key: string | null;
  broker_type: string | null;
  bot_id: string | null;
  cooldown_seconds: number;
  notify: boolean;
  notes: string | null;
};

type Ev = {
  id: string;
  level: string;
  breaker_type: string;
  user_id: string | null;
  bot_id: string | null;
  strategy_key: string | null;
  broker_type: string | null;
  triggered_value: number | null;
  threshold: number | null;
  action_taken: string;
  reason: string;
  created_at: string;
  resolved_at: string | null;
};

const emptyForm = (): Partial<Config> => ({
  level: 'global',
  breaker_type: 'maintenance_mode',
  enabled: true,
  action: 'block_new',
  value_num: null,
  unit: '',
  user_id: '',
  strategy_key: '',
  broker_type: '',
  bot_id: '',
  cooldown_seconds: 0,
  notify: true,
  notes: '',
});

export default function CircuitBreakersPage() {
  const [tab, setTab] = useState<'configs' | 'events'>('configs');
  const [levelFilter, setLevelFilter] = useState<'' | Level>('');
  const [search, setSearch] = useState('');
  const [configs, setConfigs] = useState<Config[]>([]);
  const [events, setEvents] = useState<Ev[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<Partial<Config>>(emptyForm());

  const load = useCallback(async () => {
    setErr(null);
    try {
      const [c, e] = await Promise.all([
        api.cbConfigsList(levelFilter ? { level: levelFilter } : {}),
        api.cbEventsList({ limit: 200 }),
      ]);
      setConfigs(c || []);
      setEvents(e || []);
    } catch (er: any) {
      setErr(er?.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [levelFilter]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    if (!q) return configs;
    return configs.filter((c) =>
      [c.breaker_type, c.level, c.action, c.user_id, c.strategy_key,
       c.broker_type, c.bot_id, c.notes].filter(Boolean).some((f) =>
         String(f).toLowerCase().includes(q))
    );
  }, [configs, search]);

  async function submit() {
    setErr(null);
    try {
      const payload: any = {
        level: form.level,
        breaker_type: form.breaker_type,
        enabled: form.enabled ?? true,
        action: form.action,
        value_num: form.value_num === null || form.value_num === undefined || String(form.value_num) === '' ? null : Number(form.value_num),
        unit: form.unit || null,
        user_id: form.user_id || null,
        strategy_key: form.strategy_key || null,
        broker_type: form.broker_type || null,
        bot_id: form.bot_id || null,
        cooldown_seconds: Number(form.cooldown_seconds) || 0,
        notify: form.notify ?? true,
        notes: form.notes || null,
      };
      await api.cbConfigUpsert(payload);
      setMsg('Config saved');
      setShowForm(false);
      setForm(emptyForm());
      await load();
    } catch (er: any) {
      const e = er as ApiError;
      setErr(e?.message || 'Failed to save');
    }
  }

  async function del(id: string) {
    if (!confirm('Delete this circuit breaker config?')) return;
    try {
      await api.cbConfigDelete(id);
      setMsg('Config deleted');
      await load();
    } catch (er: any) {
      setErr(er?.message || 'Failed to delete');
    }
  }

  function editRow(row: Config) {
    setForm({
      ...row,
      value_num: row.value_num,
      user_id: row.user_id || '',
      strategy_key: row.strategy_key || '',
      broker_type: row.broker_type || '',
      bot_id: row.bot_id || '',
      unit: row.unit || '',
      notes: row.notes || '',
    });
    setShowForm(true);
  }

  function exportEventsCsv() {
    const cols = ['created_at', 'level', 'breaker_type', 'user_id', 'bot_id',
                  'strategy_key', 'broker_type', 'triggered_value', 'threshold',
                  'action_taken', 'reason', 'resolved_at'];
    const rows = [cols.join(',')].concat(events.map((e) =>
      cols.map((k) => JSON.stringify((e as any)[k] ?? '')).join(',')
    ));
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'circuit_breaker_events.csv';
    a.click(); URL.revokeObjectURL(url);
  }

  return (
    <div className="page" data-testid="page-circuit-breakers">
      <div className="page-head">
        <h1>Circuit Breakers</h1>
        <p className="dim">
          Configure risk limits and kill-switch behaviour at user, strategy, broker, and global levels.
        </p>
      </div>

      <div className="tabs" style={{ marginBottom: 16 }}>
        <button
          className={`btn ${tab === 'configs' ? 'primary' : ''}`}
          onClick={() => setTab('configs')} data-testid="tab-configs"
        >Configuration ({configs.length})</button>
        <button
          className={`btn ${tab === 'events' ? 'primary' : ''}`}
          onClick={() => setTab('events')} data-testid="tab-events"
        >Audit history ({events.length})</button>
      </div>

      {err && <div className="err" data-testid="cb-error">{err}</div>}
      {msg && <Toast msg={msg} kind="ok" />}

      {tab === 'configs' && (
        <>
          <div className="toolbar">
            <select
              value={levelFilter}
              onChange={(e) => setLevelFilter(e.target.value as any)}
              data-testid="filter-level"
            >
              <option value="">All levels</option>
              {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
            </select>
            <input
              className="search"
              placeholder="Search configs…"
              value={search} onChange={(e) => setSearch(e.target.value)}
              data-testid="cb-search"
            />
            <button
              className="btn primary" onClick={() => { setForm(emptyForm()); setShowForm(true); }}
              data-testid="new-config-btn"
            >+ New config</button>
          </div>

          {showForm && (
            <div className="card" style={{ marginTop: 12, padding: 16 }} data-testid="cb-form">
              <h3>{form.id ? 'Edit config' : 'New config'}</h3>
              <div className="grid grid-2">
                <label>Level
                  <select
                    value={form.level as string}
                    onChange={(e) => setForm({ ...form, level: e.target.value as Level, breaker_type: TYPES_BY_LEVEL[e.target.value as Level][0] })}
                    data-testid="form-level"
                  >
                    {LEVELS.map((l) => <option key={l} value={l}>{l}</option>)}
                  </select>
                </label>
                <label>Type
                  <select
                    value={form.breaker_type as string}
                    onChange={(e) => setForm({ ...form, breaker_type: e.target.value })}
                    data-testid="form-type"
                  >
                    {TYPES_BY_LEVEL[form.level as Level].map((t) =>
                      <option key={t} value={t}>{t}</option>)}
                  </select>
                </label>
                <label>Action
                  <select
                    value={form.action as string}
                    onChange={(e) => setForm({ ...form, action: e.target.value })}
                    data-testid="form-action"
                  >
                    {ACTIONS.map((a) => <option key={a} value={a}>{a}</option>)}
                  </select>
                </label>
                <label>Enabled
                  <select
                    value={form.enabled ? '1' : '0'}
                    onChange={(e) => setForm({ ...form, enabled: e.target.value === '1' })}
                    data-testid="form-enabled"
                  >
                    <option value="1">Yes</option>
                    <option value="0">No</option>
                  </select>
                </label>
                <label>Value (number)
                  <input
                    type="number" step="any"
                    value={form.value_num ?? ''}
                    onChange={(e) => setForm({ ...form, value_num: e.target.value === '' ? null : Number(e.target.value) })}
                    data-testid="form-value"
                  />
                </label>
                <label>Unit (optional)
                  <input
                    value={form.unit || ''}
                    onChange={(e) => setForm({ ...form, unit: e.target.value })}
                    placeholder="cents | seconds | percent | count"
                    data-testid="form-unit"
                  />
                </label>
                <label>User id (optional)
                  <input value={form.user_id || ''} onChange={(e) => setForm({ ...form, user_id: e.target.value })} />
                </label>
                <label>Strategy key (optional)
                  <input value={form.strategy_key || ''} onChange={(e) => setForm({ ...form, strategy_key: e.target.value })} />
                </label>
                <label>Broker type (optional)
                  <input value={form.broker_type || ''} onChange={(e) => setForm({ ...form, broker_type: e.target.value })} />
                </label>
                <label>Bot id (optional)
                  <input value={form.bot_id || ''} onChange={(e) => setForm({ ...form, bot_id: e.target.value })} />
                </label>
                <label>Cooldown (seconds)
                  <input
                    type="number" min={0}
                    value={form.cooldown_seconds ?? 0}
                    onChange={(e) => setForm({ ...form, cooldown_seconds: Number(e.target.value) })}
                  />
                </label>
                <label>Notify users
                  <select
                    value={form.notify ? '1' : '0'}
                    onChange={(e) => setForm({ ...form, notify: e.target.value === '1' })}
                  >
                    <option value="1">Yes</option>
                    <option value="0">No</option>
                  </select>
                </label>
              </div>
              <label style={{ display: 'block', marginTop: 8 }}>Notes
                <textarea
                  value={form.notes || ''}
                  onChange={(e) => setForm({ ...form, notes: e.target.value })}
                  rows={2}
                />
              </label>
              <div style={{ marginTop: 12, textAlign: 'right' }}>
                <button className="btn" onClick={() => setShowForm(false)}>Cancel</button>
                <button className="btn primary" onClick={submit} data-testid="save-config-btn">Save</button>
              </div>
            </div>
          )}

          <div className="table-wrap" style={{ marginTop: 12 }}>
            <table className="tbl" data-testid="cb-table">
              <thead>
                <tr>
                  <th>Level</th><th>Type</th><th>Action</th><th>Value</th>
                  <th>Enabled</th><th>Target</th><th>Notify</th><th></th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={8} className="dim">Loading…</td></tr>}
                {!loading && filtered.length === 0 && (
                  <tr><td colSpan={8} className="dim">No configs match.</td></tr>
                )}
                {filtered.map((c) => (
                  <tr key={c.id} data-testid={`cb-row-${c.id}`}>
                    <td><Badge kind="purple">{c.level}</Badge></td>
                    <td><b>{c.breaker_type}</b></td>
                    <td>{c.action}</td>
                    <td>{c.value_num ?? '—'}{c.unit ? ` ${c.unit}` : ''}</td>
                    <td>{c.enabled ? <Badge kind="ok">on</Badge> : <Badge kind="err">off</Badge>}</td>
                    <td className="dim">
                      {[c.user_id && `u=${c.user_id.slice(0, 8)}`,
                        c.strategy_key && `s=${c.strategy_key}`,
                        c.broker_type && `b=${c.broker_type}`,
                        c.bot_id && `bot=${c.bot_id.slice(0, 8)}`]
                        .filter(Boolean).join(' ') || '—'}
                    </td>
                    <td>{c.notify ? 'yes' : 'no'}</td>
                    <td>
                      <button className="btn btn-sm" onClick={() => editRow(c)} data-testid={`edit-${c.id}`}>Edit</button>
                      <button className="btn btn-sm err" onClick={() => del(c.id)} data-testid={`delete-${c.id}`}>Delete</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {tab === 'events' && (
        <>
          <div className="toolbar">
            <span className="dim">{events.length} events</span>
            <button className="btn" onClick={exportEventsCsv} data-testid="export-events-csv">Export CSV</button>
            <button className="btn" onClick={load}>Refresh</button>
          </div>
          <div className="table-wrap">
            <table className="tbl" data-testid="cb-events-table">
              <thead>
                <tr>
                  <th>Time</th><th>Level</th><th>Type</th><th>Action</th>
                  <th>Target</th><th>Value / Threshold</th><th>Reason</th><th>Resolved</th>
                </tr>
              </thead>
              <tbody>
                {loading && <tr><td colSpan={8} className="dim">Loading…</td></tr>}
                {!loading && events.length === 0 && (
                  <tr><td colSpan={8} className="dim">No events.</td></tr>
                )}
                {events.map((e) => (
                  <tr key={e.id}>
                    <td>{fmtDate(e.created_at)}</td>
                    <td><Badge kind="purple">{e.level}</Badge></td>
                    <td><b>{e.breaker_type}</b></td>
                    <td>{e.action_taken}</td>
                    <td className="dim">
                      {[e.user_id && `u=${e.user_id.slice(0, 8)}`,
                        e.bot_id && `bot=${e.bot_id.slice(0, 8)}`,
                        e.strategy_key, e.broker_type]
                        .filter(Boolean).join(' ') || '—'}
                    </td>
                    <td>
                      {e.triggered_value ?? '—'} / {e.threshold ?? '—'}
                    </td>
                    <td className="dim">{e.reason || '—'}</td>
                    <td>{e.resolved_at ? <Badge kind="ok">{fmtDate(e.resolved_at)}</Badge> : <Badge kind="warn">open</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  );
}
