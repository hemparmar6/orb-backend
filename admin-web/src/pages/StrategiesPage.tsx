import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Modal, Pagination, fmtDate, statusBadge, Toast } from '../ui';

export default function StrategiesPage() {
  const [q, setQ] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [registered, setRegistered] = useState<any[]>([]);
  const [inspect, setInspect] = useState<any>(null);
  const [toast, setToast] = useState<any>(null);

  const load = useCallback(async () => {
    try {
      const r = await api.listStrategies({ page, page_size: pageSize });
      const items = q ? r.items.filter((s: any) => (s.name || '').toLowerCase().includes(q.toLowerCase())) : r.items;
      setData({ ...r, items });
    } catch (e: any) { setToast({ msg: e.message, kind: 'err' }); }
  }, [q, page]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => { api.listRegisteredStrategies().then(setRegistered).catch(() => {}); }, []);

  return (
    <div>
      <div className="section-title">Registered Strategy Classes ({registered.length})</div>
      <div className="table-wrap" data-testid="registered-strategies-table">
        <table className="table">
          <thead><tr><th>NAME</th><th>CLASS</th><th>MODULE</th></tr></thead>
          <tbody>
            {registered.length === 0 ? (
              <tr><td colSpan={3} className="table-empty">None registered.</td></tr>
            ) : registered.map((r: any) => (
              <tr key={r.name}>
                <td>{statusBadge(r.name)}</td>
                <td className="mono">{r.class_name}</td>
                <td className="mono muted">{r.module}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="section-title" style={{ marginTop: 24 }}>User Strategies</div>
      <div className="toolbar">
        <input data-testid="strategies-search-input" placeholder="Search name…" value={q} onChange={(e) => setQ(e.target.value)} />
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="strategies-refresh-button">↻ Refresh</button>
      </div>
      <div className="table-wrap" data-testid="strategies-table">
        <table className="table">
          <thead><tr><th>ID</th><th>USER</th><th>NAME</th><th>STATUS</th><th>PUBLIC</th><th>CREATED</th><th></th></tr></thead>
          <tbody>
            {data.items.length === 0 ? (
              <tr><td colSpan={7} className="table-empty">No user strategies yet.</td></tr>
            ) : data.items.map((s: any) => (
              <tr key={s.id}>
                <td className="mono">{s.id.slice(0, 8)}</td>
                <td className="mono">{s.user_id.slice(0, 8)}</td>
                <td>{s.name}</td>
                <td>{statusBadge(s.status)}</td>
                <td>{s.is_public ? 'yes' : 'no'}</td>
                <td className="mono">{fmtDate(s.created_at)}</td>
                <td><button className="btn btn-sm" onClick={() => setInspect(s)} data-testid={`strategy-inspect-${s.id}`}>Inspect</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="strategies-pagination" />

      {inspect && (
        <Modal title={`Strategy: ${inspect.name}`} onClose={() => setInspect(null)} testId="strategy-inspect-modal">
          <div className="kv">
            <div className="k">ID</div><div className="v">{inspect.id}</div>
            <div className="k">User</div><div className="v">{inspect.user_id}</div>
            <div className="k">Status</div><div className="v">{inspect.status}</div>
            <div className="k">Description</div><div className="v">{inspect.description || '—'}</div>
            <div className="k">Created</div><div className="v">{fmtDate(inspect.created_at)}</div>
          </div>
          <div className="section-title">Parameters</div>
          <pre className="json">{JSON.stringify(inspect.parameters || {}, null, 2)}</pre>
        </Modal>
      )}
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
