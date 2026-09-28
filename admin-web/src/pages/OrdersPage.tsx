import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function OrdersPage() {
  const [userId, setUserId] = useState('');
  const [sessionId, setSessionId] = useState('');
  const [symbol, setSymbol] = useState('');
  const [status, setStatus] = useState('');
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
      if (status) p.status = status;
      setData(await api.listOrders(p));
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [userId, sessionId, symbol, status, page]);

  useEffect(() => { load(); }, [load]);

  return (
    <div>
      <div className="toolbar" data-testid="orders-toolbar">
        <input data-testid="orders-user-filter" placeholder="user_id" value={userId} onChange={(e) => { setPage(1); setUserId(e.target.value); }} />
        <input data-testid="orders-session-filter" placeholder="session_id" value={sessionId} onChange={(e) => { setPage(1); setSessionId(e.target.value); }} />
        <input data-testid="orders-symbol-filter" placeholder="symbol" value={symbol} onChange={(e) => { setPage(1); setSymbol(e.target.value); }} />
        <select data-testid="orders-status-filter" value={status} onChange={(e) => { setPage(1); setStatus(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="submitted">Submitted</option>
          <option value="filled">Filled</option>
          <option value="partially_filled">Partial</option>
          <option value="cancelled">Cancelled</option>
          <option value="rejected">Rejected</option>
        </select>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="orders-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="orders-table">
        <table className="table">
          <thead><tr>
            <th>ID</th><th>USER</th><th>SESSION</th><th>SYMBOL</th><th>SIDE</th><th>TYPE</th><th>QTY</th><th>FILLED</th><th>LIMIT</th><th>AVG</th><th>STATUS</th><th>CREATED</th>
          </tr></thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={12} className="table-empty">No orders.</td></tr>
            ) : data.items.map((o: any) => (
              <tr key={o.id}>
                <td className="mono">{o.id.slice(0, 8)}</td>
                <td className="mono">{o.user_id.slice(0, 8)}</td>
                <td className="mono">{(o.engine_session_id || '').slice(0, 8) || '—'}</td>
                <td>{o.symbol}</td>
                <td>{statusBadge(o.side)}</td>
                <td className="mono">{o.order_type}</td>
                <td className="mono">{o.quantity}</td>
                <td className="mono">{o.filled_quantity}</td>
                <td className="mono">{o.limit_price ?? '—'}</td>
                <td className="mono">{o.avg_fill_price ?? '—'}</td>
                <td>{statusBadge(o.status)}</td>
                <td className="mono">{fmtDate(o.created_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="orders-pagination" />
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
