// Admin — Kill Switch History (filter / paginate / export).
import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Badge, fmtDate, Pagination } from '../ui';

type Ev = {
  id: string;
  scope: 'bot' | 'user' | 'global';
  target_user_id: string | null;
  target_bot_id: string | null;
  reason: string;
  bots_stopped: number;
  created_at: string;
  resolved_at: string | null;
  actor_user_id: string | null;
};

const SCOPES = ['', 'bot', 'user', 'global'] as const;
const PAGE_SIZE = 25;

export default function KillSwitchHistoryPage() {
  const [events, setEvents] = useState<Ev[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [scope, setScope] = useState<string>('');
  const [active, setActive] = useState<boolean>(false);
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [busy, setBusy] = useState<string | null>(null);
  const [triggerOpen, setTriggerOpen] = useState(false);
  const [triggerReason, setTriggerReason] = useState('');

  const load = useCallback(async () => {
    setErr(null);
    try {
      const rows = await api.ksEvents({ active_only: active, limit: 500 });
      setEvents(rows || []);
      setPage(1);
    } catch (e: any) {
      setErr(e?.message || 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [active]);

  useEffect(() => { load(); }, [load]);

  const filtered = useMemo(() => {
    let rows = events;
    if (scope) rows = rows.filter((e) => e.scope === scope);
    const q = search.trim().toLowerCase();
    if (q) rows = rows.filter((e) =>
      [e.reason, e.target_user_id, e.target_bot_id, e.actor_user_id, e.scope]
        .filter(Boolean).some((f) => String(f).toLowerCase().includes(q))
    );
    return rows;
  }, [events, scope, search]);

  const pageRows = filtered.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  async function resolveEv(id: string) {
    if (!confirm('Mark this kill-switch event as resolved?')) return;
    setBusy(id);
    try {
      await api.ksResolve(id);
      await load();
    } catch (e: any) {
      setErr(e?.message || 'Failed to resolve');
    } finally {
      setBusy(null);
    }
  }

  async function triggerGlobalKill() {
    if (!triggerReason.trim()) { setErr('Reason required'); return; }
    if (!confirm('Trigger GLOBAL kill switch? This will stop ALL bots for ALL users immediately.')) return;
    setBusy('global');
    try {
      await api.ksTrigger({ scope: 'global', reason: triggerReason });
      setTriggerReason('');
      setTriggerOpen(false);
      await load();
    } catch (e: any) {
      setErr(e?.message || 'Failed to trigger');
    } finally {
      setBusy(null);
    }
  }

  function exportCsv() {
    const cols = ['created_at', 'scope', 'target_user_id', 'target_bot_id',
                  'reason', 'bots_stopped', 'actor_user_id', 'resolved_at'];
    const rows = [cols.join(',')].concat(filtered.map((e) =>
      cols.map((k) => JSON.stringify((e as any)[k] ?? '')).join(',')
    ));
    const blob = new Blob([rows.join('\n')], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url; a.download = 'kill_switch_history.csv';
    a.click(); URL.revokeObjectURL(url);
  }

  const summary = useMemo(() => {
    const total = events.length;
    const open = events.filter((e) => !e.resolved_at).length;
    const global_c = events.filter((e) => e.scope === 'global').length;
    const user_c = events.filter((e) => e.scope === 'user').length;
    const bot_c = events.filter((e) => e.scope === 'bot').length;
    return { total, open, global_c, user_c, bot_c };
  }, [events]);

  return (
    <div className="page" data-testid="page-kill-switch-history">
      <div className="page-head">
        <h1>Kill Switch History</h1>
        <p className="dim">
          Emergency stops across users and bots — manual and automatic triggers, full audit trail.
        </p>
      </div>

      <div className="grid grid-4">
        <div className="card"><div className="card-title">Total events</div><div className="card-value">{summary.total}</div></div>
        <div className="card"><div className="card-title">Open</div><div className="card-value" style={{ color: 'var(--warn)' }}>{summary.open}</div></div>
        <div className="card"><div className="card-title">Global</div><div className="card-value">{summary.global_c}</div></div>
        <div className="card"><div className="card-title">User / Bot</div><div className="card-value">{summary.user_c} / {summary.bot_c}</div></div>
      </div>

      {err && <div className="err" style={{ marginTop: 12 }} data-testid="ks-error">{err}</div>}

      <div className="toolbar" style={{ marginTop: 16 }}>
        <select value={scope} onChange={(e) => setScope(e.target.value)} data-testid="filter-scope">
          {SCOPES.map((sc) => <option key={sc || 'all'} value={sc}>{sc || 'All scopes'}</option>)}
        </select>
        <label className="chk">
          <input type="checkbox" checked={active} onChange={(e) => setActive(e.target.checked)} data-testid="filter-active" />
          &nbsp;Active only
        </label>
        <input
          className="search"
          placeholder="Search reason, user, bot…"
          value={search} onChange={(e) => setSearch(e.target.value)}
          data-testid="ks-search"
        />
        <button className="btn" onClick={exportCsv} data-testid="export-ks-csv">Export CSV</button>
        <button className="btn" onClick={load}>Refresh</button>
        <button
          className="btn err" onClick={() => setTriggerOpen(true)}
          data-testid="global-kill-btn"
        >Trigger global kill</button>
      </div>

      {triggerOpen && (
        <div className="card" style={{ padding: 16, marginTop: 12, borderColor: 'var(--err)' }}>
          <h3 style={{ color: 'var(--err)' }}>Global kill switch</h3>
          <p className="dim">All active bots across the platform will be stopped immediately.</p>
          <label style={{ display: 'block' }}>Reason (required)
            <input
              value={triggerReason} onChange={(e) => setTriggerReason(e.target.value)}
              placeholder="e.g. broker outage / market halt"
              data-testid="global-kill-reason"
            />
          </label>
          <div style={{ marginTop: 12, textAlign: 'right' }}>
            <button className="btn" onClick={() => setTriggerOpen(false)}>Cancel</button>
            <button
              className="btn err" onClick={triggerGlobalKill}
              disabled={busy === 'global'}
              data-testid="confirm-global-kill"
            >{busy === 'global' ? 'Working…' : 'KILL NOW'}</button>
          </div>
        </div>
      )}

      <div className="table-wrap" style={{ marginTop: 12 }}>
        <table className="tbl" data-testid="ks-table">
          <thead>
            <tr>
              <th>Time</th>
              <th>Scope</th>
              <th>Target</th>
              <th>Reason</th>
              <th>Source</th>
              <th>Bots stopped</th>
              <th>Operator</th>
              <th>Resolved</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {loading && <tr><td colSpan={9} className="dim">Loading…</td></tr>}
            {!loading && pageRows.length === 0 && (
              <tr><td colSpan={9} className="dim">No events match filters.</td></tr>
            )}
            {pageRows.map((e) => {
              const auto = /breaker|automated|automation|monitor/i.test(e.reason || '');
              return (
                <tr key={e.id} data-testid={`ks-row-${e.id}`}>
                  <td>{fmtDate(e.created_at)}</td>
                  <td>
                    {e.scope === 'global'
                      ? <Badge kind="err">global</Badge>
                      : e.scope === 'user'
                        ? <Badge kind="warn">user</Badge>
                        : <Badge kind="info">bot</Badge>}
                  </td>
                  <td className="dim">
                    {e.target_user_id ? `u=${e.target_user_id.slice(0, 8)}` : '—'}
                    {e.target_bot_id ? ` bot=${e.target_bot_id.slice(0, 8)}` : ''}
                  </td>
                  <td>{e.reason || '—'}</td>
                  <td>{auto ? <Badge kind="warn">auto</Badge> : <Badge>manual</Badge>}</td>
                  <td>{e.bots_stopped}</td>
                  <td className="dim">{e.actor_user_id ? e.actor_user_id.slice(0, 8) : '—'}</td>
                  <td>{e.resolved_at
                    ? <Badge kind="ok">{fmtDate(e.resolved_at)}</Badge>
                    : <Badge kind="warn">open</Badge>}</td>
                  <td>
                    {!e.resolved_at && (
                      <button
                        className="btn btn-sm" onClick={() => resolveEv(e.id)}
                        disabled={busy === e.id}
                        data-testid={`resolve-${e.id}`}
                      >Resolve</button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      <Pagination
        page={page} pageSize={PAGE_SIZE} total={filtered.length}
        onPage={setPage} testId="ks-pagination"
      />
    </div>
  );
}
