import { useCallback, useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import { Pagination, Toast, fmtDate, statusBadge } from '../ui';
import { useAdminSocket } from '../useAdminSocket';
import { LiveBadge } from '../LiveBadge';

export default function SessionsPage() {
  const [status, setStatus] = useState('');
  const [mode, setMode] = useState('');
  const [userId, setUserId] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [toast, setToast] = useState<any>(null);

  // Live snapshot — used to freshen the running-session rows in-place.
  const { snapshot, state: wsState, lastUpdatedAt } = useAdminSocket(3);

  const load = useCallback(async () => {
    try {
      const p: any = { page, page_size: pageSize };
      if (status) p.status = status;
      if (mode) p.execution_mode = mode;
      if (userId) p.user_id = userId;
      setData(await api.listSessions(p));
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [status, mode, userId, page]);

  useEffect(() => { load(); }, [load]);

  // When the live snapshot shows a change in running-session count, refresh
  // the paginated listing so admins never see stale state.
  const runningCount = snapshot?.health?.engine_sessions_running ?? null;
  useEffect(() => {
    if (runningCount === null) return;
    if (page === 1) { load(); }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runningCount]);

  // Merge: live snapshot data for RUNNING rows, REST data for the rest.
  const merged = useMemo(() => {
    const liveById: Record<string, any> = {};
    for (const r of snapshot?.running_sessions || []) liveById[r.id] = r;
    return (data.items || []).map((row: any) => {
      const live = liveById[row.id];
      if (!live) return row;
      return {
        ...row,
        status: live.status ?? row.status,
        last_heartbeat_at: live.last_heartbeat_at ?? row.last_heartbeat_at,
        day_pnl: live.day_pnl ?? row.day_pnl ?? 0,
        _live: true,
      };
    });
  }, [data.items, snapshot]);

  async function stop(id: string) {
    if (!confirm('Force-stop this session?')) return;
    try { await api.stopSession(id); setToast({ msg: 'Session stopped', kind: 'ok' }); load(); }
    catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }

  return (
    <div>
      <div className="toolbar" data-testid="sessions-toolbar">
        <input data-testid="sessions-user-filter" placeholder="Filter by user_id…" value={userId} onChange={(e) => { setPage(1); setUserId(e.target.value); }} />
        <select data-testid="sessions-status-filter" value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="running">Running</option>
          <option value="stopped">Stopped</option>
          <option value="error">Error</option>
        </select>
        <select data-testid="sessions-mode-filter" value={mode} onChange={(e) => { setPage(1); setMode(e.target.value); }}>
          <option value="">All modes</option>
          <option value="paper">Paper</option>
          <option value="live">Live</option>
        </select>
        <div className="spacer" />
        <LiveBadge state={wsState} lastUpdatedAt={lastUpdatedAt} testId="sessions-live-badge" />
        <button className="btn btn-sm" onClick={load} data-testid="sessions-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="sessions-table">
        <table className="table">
          <thead>
            <tr>
              <th>SESSION</th><th>USER</th><th>STRATEGY</th><th>MODE</th><th>SYMBOLS</th><th>CAPITAL</th><th>DAY P&L</th><th>STATUS</th><th>STARTED</th><th>HEARTBEAT</th><th></th>
            </tr>
          </thead>
          <tbody>
            {merged.length === 0 ? (
              <tr><td colSpan={11} className="table-empty">No sessions.</td></tr>
            ) : merged.map((s: any) => (
              <tr key={s.id} data-testid={`session-row-${s.id}`}>
                <td className="mono">{String(s.id).slice(0, 8)}</td>
                <td className="mono">{String(s.user_id).slice(0, 8)}</td>
                <td>{s.strategy_name}</td>
                <td>{statusBadge(s.execution_mode)}</td>
                <td className="mono">{(s.symbols || []).join(', ')}</td>
                <td className="mono">{Number(s.initial_capital).toLocaleString()}</td>
                <td className={`mono ${Number(s.day_pnl || 0) >= 0 ? 'pos' : 'neg'}`}>{Number(s.day_pnl || 0).toFixed(2)}</td>
                <td>{statusBadge(s.status)}</td>
                <td className="mono">{fmtDate(s.started_at)}</td>
                <td className="mono">{fmtDate(s.last_heartbeat_at)}</td>
                <td>
                  {s.status === 'running' && (
                    <button className="btn btn-sm btn-danger" onClick={() => stop(s.id)} data-testid={`session-stop-${s.id}`}>Stop</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="sessions-pagination" />
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
