import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function TradesPage() {
  const [userId, setUserId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 50;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [toast, setToast] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const p: any = { page, page_size: pageSize };
      if (userId) p.user_id = userId;
      if (sessionId) p.session_id = sessionId;
      if (symbol) p.symbol = symbol;
      setData(await api.listTrades(p));
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [userId, sessionId, symbol, page]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="toolbar" data-testid="trades-toolbar">
        <input data-testid="trades-user-filter" placeholder="user_id" value={userId} onChange={(e) => { setPage(1); setUserId(e.target.value); }} />
        <input data-testid="trades-session-filter" placeholder="session_id" value={sessionId} onChange={(e) => { setPage(1); setSessionId(e.target.value); }} />
        <input data-testid="trades-symbol-filter" placeholder="symbol" value={symbol} onChange={(e) => { setPage(1); setSymbol(e.target.value); }} />
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="trades-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="trades-table">
        <table className="table">
          <thead><tr>
            <th>ID</th><th>USER</th><th>SESSION</th><th>ORDER</th><th>SYMBOL</th><th>SIDE</th><th>QTY</th><th>PRICE</th><th>FEES</th><th>CREATED</th>
          </tr></thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={10} className="table-empty">No trades.</td></tr>
            ) : data.items.map((t: any) => (
              <tr key={t.id}>
                <td className="mono">{t.id.slice(0, 8)}</td>
                <td className="mono">{t.user_id.slice(0, 8)}</td>
                <td className="mono">{(t.engine_session_id || '').slice(0, 8) || '—'}</td>
                <td className="mono">{(t.order_id || '').slice(0, 8) || '—'}</td>
                <td>{t.symbol}</td>
                <td>{statusBadge(t.side)}</td>
                <td className="mono">{t.quantity}</td>
                <td className="mono">{t.price}</td>
                <td className="mono">{t.fees}</td>
                <td className="mono">{fmtDate(t.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="trades-pagination" />
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
