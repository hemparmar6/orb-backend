/**
 * Persistent Trading-Mode badge for the app shell.  Always rendered from
 * the shared TradingModeContext, which mirrors the server-authoritative
 * snapshot.  On error we render an "unknown / safe" state — never LIVE.
 */
import { useTradingMode } from './TradingModeContext';

export function TradingModeBadge({ testId = 'trading-mode-badge' }: { testId?: string }) {
  const { snapshot, loading, errored } = useTradingMode();

  if (loading && !snapshot) {
    return (
      <span className="badge" data-testid={testId} data-mode="loading">
        <span className="status-dot" style={{ marginRight: 6 }} />
        Mode…
      </span>
    );
  }
  if (errored && !snapshot) {
    return (
      <span
        className="badge warn"
        data-testid={testId}
        data-mode="unknown"
        title="Trading mode unavailable — treated as PAPER"
      >
        <span className="status-dot warn" style={{ marginRight: 6 }} />
        Mode ?
      </span>
    );
  }
  const mode = snapshot?.mode === 'live' ? 'live' : 'paper';
  const cooldown = !!snapshot?.cooldown_active;
  const kind = mode === 'live' ? (cooldown ? 'warn' : 'err') : 'purple';
  const label =
    mode === 'live'
      ? cooldown
        ? 'LIVE · cooldown'
        : 'LIVE'
      : 'PAPER';
  return (
    <span
      className={`badge ${kind}`}
      data-testid={testId}
      data-mode={mode}
      data-cooldown={cooldown ? 'active' : 'inactive'}
      title={
        mode === 'live'
          ? 'Real-money execution enabled — additional safety gates still apply'
          : 'Paper trading — no real-money orders'
      }
    >
      <span className={`status-dot ${kind}`} style={{ marginRight: 6 }} />
      {label}
    </span>
  );
}
