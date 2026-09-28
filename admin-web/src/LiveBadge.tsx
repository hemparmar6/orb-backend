import { ConnState } from './useAdminSocket';

export function LiveBadge({ state, lastUpdatedAt, testId }: { state: ConnState; lastUpdatedAt: number | null; testId?: string }) {
  const cls =
    state === 'open' ? 'ok' :
    state === 'connecting' ? 'warn' :
    'err';
  const label =
    state === 'open' ? 'LIVE' :
    state === 'connecting' ? 'CONNECTING' :
    state === 'error' ? 'OFFLINE' :
    'RECONNECTING';

  const ageSec = lastUpdatedAt ? Math.max(0, Math.round((Date.now() - lastUpdatedAt) / 1000)) : null;
  return (
    <span className={`badge ${cls}`} data-testid={testId} title={ageSec !== null ? `Last update ${ageSec}s ago` : 'No data yet'}>
      <span className={`status-dot ${cls}`} style={{ marginRight: 6, verticalAlign: 'middle' }} />
      {label}{ageSec !== null && state === 'open' ? ` · ${ageSec}s` : ''}
    </span>
  );
}
