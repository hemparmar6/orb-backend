import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function BrokersPage() {
  const [q, setQ] = useState('');
  const [type, setType] = useState('');
  const [active, setActive] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [catalog, setCatalog] = useState<any[]>([]);
  const [toast, setToast] = useState<{ msg: string; kind: 'ok' | 'err' } | null>(null);

  const load = useCallback(async () => {
    try {
      const p: any = { page, page_size: pageSize };
      if (type) p.broker_type = type;
      if (active) p.is_active = active === 'true';
      const r = await api.listBrokerAccounts(p);
      // client-side alias/user_id filter
      const items = q
        ? r.items.filter((x: any) =>
            (x.alias || '').toLowerCase().includes(q.toLowerCase()) ||
            x.user_id.toLowerCase().includes(q.toLowerCase()))
        : r.items;
      setData({ ...r, items });
    } catch (e: any) { setToast({ msg: e.message || 'Load failed', kind: 'err' }); }
  }, [q, type, active, page]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.brokerCatalog().then((c) => setCatalog(c || [])).catch(() => {}); }, []);

  async function disconnect(id: string) {
    if (!confirm('Disconnect this broker account?')) return;
    try { await api.deleteBrokerAccount(id); setToast({ msg: 'Disconnected', kind: 'ok' }); load(); }
    catch (e: any) { setToast({ msg: e.message || 'Failed', kind: 'err' }); }
  }

  return (
    <div>
      <div className="toolbar" data-testid="brokers-toolbar">
        <input data-testid="brokers-search-input" placeholder="Search alias / user_id…" value={q} onChange={(e) => setQ(e.target.value)} />
        <select data-testid="brokers-type-filter" value={type} onChange={(e) => { setPage(1); setType(e.target.value); }}>
          <option value="">All broker types</option>
          {catalog.map((b) => <option key={b.name || b.broker_type} value={b.name || b.broker_type}>{b.display_name || b.name || b.broker_type}</option>)}
        </select>
        <select data-testid="brokers-active-filter" value={active} onChange={(e) => { setPage(1); setActive(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="true">Active</option>
          <option value="false">Inactive</option>
        </select>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="brokers-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="brokers-table">
        <table className="table">
          <thead>
            <tr><th>ID</th><th>USER</th><th>BROKER</th><th>ALIAS</th><th>STATUS</th><th>LAST USED</th><th>CREATED</th><th>ACTIONS</th></tr>
          </thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={8} className="table-empty">No broker accounts.</td></tr>
            ) : data.items.map((b: any) => (
              <tr key={b.id} data-testid={`broker-row-${b.id}`}>
                <td className="mono">{b.id.slice(0, 8)}</td>
                <td className="mono">{b.user_id.slice(0, 8)}</td>
                <td>{statusBadge(b.broker_type)}</td>
                <td>{b.alias || '—'}</td>
                <td>{b.is_active ? statusBadge('active') : statusBadge('inactive')}</td>
                <td className="mono">{fmtDate(b.last_used_at)}</td>
                <td className="mono">{fmtDate(b.created_at)}</td>
                <td>
                  <button className="btn btn-sm btn-danger" onClick={() => disconnect(b.id)} data-testid={`broker-disconnect-${b.id}`}>Disconnect</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="brokers-pagination" />
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
