import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Modal, Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function BacktestsPage() {
  const [userId, setUserId] = useState('');
  const [strategyName, setStrategyName] = useState('');
  const [status, setStatus] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [inspect, setInspect] = useState<any>(null);
  const [inspectData, setInspectData] = useState<any>(null);
  const [toast, setToast] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const p: any = { page, page_size: pageSize };
      if (userId) p.user_id = userId;
      if (strategyName) p.strategy_name = strategyName;
      if (status) p.status = status;
      setData(await api.listBacktests(p));
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [userId, strategyName, status, page]);

  useEffect(() => { load(); }, [load]);

  async function inspectRun(row: any) {
    setInspect(row); setInspectData(null);
    try {
      const tok = localStorage.getItem('orb_admin_access_token');
      const r = await fetch(`/api/v1/backtest/${row.id}/results`, {
        headers: { Authorization: `Bearer ${tok}` },
      });
      if (r.ok) setInspectData(await r.json());
      else setInspectData({ error: `HTTP ${r.status}` });
    } catch (e: any) { setInspectData({ error: e?.message || 'load failed' }); }
  }

  return (
    <div>
      <div className="toolbar" data-testid="backtests-toolbar">
        <input data-testid="backtests-user-filter" placeholder="user_id" value={userId} onChange={(e) => { setPage(1); setUserId(e.target.value); }} />
        <input data-testid="backtests-strategy-filter" placeholder="strategy name (e.g. orb)" value={strategyName} onChange={(e) => { setPage(1); setStrategyName(e.target.value); }} />
        <select data-testid="backtests-status-filter" value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="queued">Queued</option>
          <option value="running">Running</option>
          <option value="complete">Complete</option>
          <option value="failed">Failed</option>
        </select>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="backtests-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="backtests-table">
        <table className="table">
          <thead><tr>
            <th>ID</th><th>USER</th><th>STRATEGY</th><th>SYMBOLS</th><th>PERIOD</th><th>CAPITAL</th><th>STATUS</th><th>FINISHED</th><th></th>
          </tr></thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={9} className="table-empty">No backtest runs.</td></tr>
            ) : data.items.map((b: any) => (
              <tr key={b.id}>
                <td className="mono">{b.id.slice(0, 8)}</td>
                <td className="mono">{b.user_id.slice(0, 8)}</td>
                <td>{b.strategy_name}</td>
                <td className="mono">{(b.symbols || []).join(', ')}</td>
                <td className="mono">{b.start_date} → {b.end_date}</td>
                <td className="mono">{Number(b.initial_capital).toLocaleString()}</td>
                <td>{statusBadge(b.status)}</td>
                <td className="mono">{fmtDate(b.finished_at)}</td>
                <td><button className="btn btn-sm" onClick={() => inspectRun(b)} data-testid={`backtest-inspect-${b.id}`}>Results</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="backtests-pagination" />

      {inspect && (
        <Modal title={`Backtest · ${inspect.strategy_name}`} onClose={() => { setInspect(null); setInspectData(null); }} testId="backtest-modal">
          <div className="kv">
            <div className="k">ID</div><div className="v">{inspect.id}</div>
            <div className="k">Status</div><div className="v">{inspect.status}</div>
            <div className="k">Period</div><div className="v">{inspect.start_date} → {inspect.end_date}</div>
            <div className="k">Capital</div><div className="v">{Number(inspect.initial_capital).toLocaleString()}</div>
            <div className="k">Error</div><div className="v">{inspect.error_message || '—'}</div>
          </div>
          <div className="section-title">Metrics</div>
          {!inspectData ? <div className="dim">Loading…</div> : (
            <pre className="json">{JSON.stringify(inspectData?.metrics ?? inspectData, null, 2)}</pre>
          )}
        </Modal>
      )}
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
