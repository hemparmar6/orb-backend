import { useCallback, useEffect, useState } from 'react';
import { api } from '../api';
import { Pagination, Toast, fmtDate, statusBadge } from '../ui';

export default function UsersPage() {
  const [q, setQ] = useState('');
  const [role, setRole] = useState('');
  const [active, setActive] = useState('');
  const [page, setPage] = useState(1);
  const pageSize = 25;
  const [data, setData] = useState<any>({ items: [], total: 0 });
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<{ msg: string; kind: 'ok' | 'err' } | null>(null);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const p: any = { page, page_size: pageSize };
      if (q) p.q = q;
      if (role) p.role = role;
      if (active) p.is_active = active === 'true';
      const r = await api.listUsers(p);
      setData(r);
    } catch (e: any) {
      setToast({ msg: e.message || 'Load failed', kind: 'err' });
    } finally { setBusy(false); }
  }, [q, role, active, page]);

  useEffect(() => { load(); }, [load]);

  async function toggleActive(u: any) {
    try {
      await api.patchUser(u.id, { is_active: !u.is_active });
      setToast({ msg: `User ${!u.is_active ? 'enabled' : 'disabled'}`, kind: 'ok' });
      load();
    } catch (e: any) {
      setToast({ msg: e.message || 'Update failed', kind: 'err' });
    }
  }
  async function toggleRole(u: any) {
    try {
      const newRole = u.role === 'admin' ? 'user' : 'admin';
      await api.patchUser(u.id, { role: newRole });
      setToast({ msg: `Role → ${newRole}`, kind: 'ok' });
      load();
    } catch (e: any) {
      setToast({ msg: e.message || 'Update failed', kind: 'err' });
    }
  }

  return (
    <div>
      <div className="toolbar" data-testid="users-toolbar">
        <input data-testid="users-search-input" placeholder="Search email / name…" value={q} onChange={(e) => { setPage(1); setQ(e.target.value); }} />
        <select data-testid="users-role-filter" value={role} onChange={(e) => { setPage(1); setRole(e.target.value); }}>
          <option value="">All roles</option>
          <option value="user">User</option>
          <option value="admin">Admin</option>
        </select>
        <select data-testid="users-active-filter" value={active} onChange={(e) => { setPage(1); setActive(e.target.value); }}>
          <option value="">All statuses</option>
          <option value="true">Active</option>
          <option value="false">Disabled</option>
        </select>
        <div className="spacer" />
        <button className="btn btn-sm" onClick={load} data-testid="users-refresh-button">↻ Refresh</button>
      </div>

      <div className="table-wrap" data-testid="users-table">
        <table className="table">
          <thead>
            <tr>
              <th>EMAIL</th><th>NAME</th><th>ROLE</th><th>STATUS</th><th>VERIFIED</th><th>LAST LOGIN</th><th>CREATED</th><th>ACTIONS</th>
            </tr>
          </thead>
          <tbody>
            {busy && !data.items.length ? (
              <tr><td colSpan={8} className="table-empty">Loading…</td></tr>
            ) : data.items.length === 0 ? (
              <tr><td colSpan={8} className="table-empty">No users match.</td></tr>
            ) : data.items.map((u: any) => (
              <tr key={u.id} data-testid={`user-row-${u.id}`}>
                <td>{u.email}</td>
                <td>{u.full_name || '—'}</td>
                <td>{statusBadge(u.role)}</td>
                <td>{u.is_active ? statusBadge('active') : statusBadge('inactive')}</td>
                <td>{u.is_verified ? '✓' : '—'}</td>
                <td className="mono">{fmtDate(u.last_login_at)}</td>
                <td className="mono">{fmtDate(u.created_at)}</td>
                <td>
                  <button className="btn btn-sm" onClick={() => toggleActive(u)} data-testid={`user-toggle-active-${u.id}`}>
                    {u.is_active ? 'Disable' : 'Enable'}
                  </button>{' '}
                  <button className="btn btn-sm" onClick={() => toggleRole(u)} data-testid={`user-toggle-role-${u.id}`}>
                    {u.role === 'admin' ? 'Demote' : 'Promote'}
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Pagination page={page} pageSize={pageSize} total={data.total || 0} onPage={setPage} testId="users-pagination" />
      {toast && <Toast msg={toast.msg} kind={toast.kind} />}
    </div>
  );
}
