/**
 * Persistent Trading Mode badge — safety-critical tests:
 *   1. PAPER badge renders from server state.
 *   2. LIVE badge renders from server state.
 *   3. Failed API request NEVER falsely reports LIVE.
 *   4. Mode changes update the badge (via the shared context refresh).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { TradingModeProvider, useTradingMode } from '../TradingModeContext';
import { TradingModeBadge } from '../TradingModeBadge';
import { tokenStore } from '../api';

function paperSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    mode: 'paper',
    active_live_sessions: 0,
    open_live_positions: 0,
    open_live_orders: 0,
    kill_switch_active: false,
    live_gate: 'requires_existing_live_safety_checks',
    cooldown_active: false,
    auto_revert: { enabled: false },
    ...overrides,
  };
}
function liveSnapshot(overrides: Record<string, unknown> = {}) {
  return paperSnapshot({ mode: 'live', armed_at: '2026-02-05T10:00:00Z', ...overrides });
}
function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  return {
    ok: init.ok ?? true,
    status: init.status ?? 200,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

beforeEach(() => {
  tokenStore.set('test-access-token');
});

function Harness() {
  return (
    <TradingModeProvider authenticated={true}>
      <TradingModeBadge />
    </TradingModeProvider>
  );
}

describe('TradingModeBadge', () => {
  it('renders PAPER badge when server returns PAPER', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(paperSnapshot())) as unknown as typeof fetch);
    render(<Harness />);
    const badge = await waitFor(() => {
      const b = screen.getByTestId('trading-mode-badge');
      expect(b).toHaveAttribute('data-mode', 'paper');
      return b;
    });
    expect(badge).toHaveTextContent('PAPER');
  });

  it('renders LIVE badge when server returns LIVE', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(liveSnapshot())) as unknown as typeof fetch);
    render(<Harness />);
    await waitFor(() => {
      const b = screen.getByTestId('trading-mode-badge');
      expect(b).toHaveAttribute('data-mode', 'live');
      expect(b).toHaveTextContent('LIVE');
    });
  });

  it('distinguishes LIVE·cooldown from plain LIVE', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(liveSnapshot({ cooldown_active: true }))) as unknown as typeof fetch,
    );
    render(<Harness />);
    await waitFor(() => {
      const b = screen.getByTestId('trading-mode-badge');
      expect(b).toHaveAttribute('data-cooldown', 'active');
      expect(b).toHaveTextContent(/LIVE · cooldown/);
    });
  });

  it('NEVER falsely reports LIVE when the API request fails', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse({ detail: 'boom' }, { ok: false, status: 500 })) as unknown as typeof fetch,
    );
    render(<Harness />);
    await waitFor(() => {
      const b = screen.getByTestId('trading-mode-badge');
      expect(b).toHaveAttribute('data-mode', 'unknown');
      expect(b).not.toHaveTextContent(/^LIVE$/);
    });
  });

  it('updates the badge after a mode change (context.refresh)', async () => {
    let currentMode: any = paperSnapshot();
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => jsonResponse(currentMode)) as unknown as typeof fetch,
    );

    function RefreshHarness() {
      const { refresh } = useTradingMode();
      return (
        <>
          <TradingModeBadge />
          <button
            data-testid="force-refresh"
            onClick={() => { currentMode = liveSnapshot(); void refresh(); }}
          >
            change mode
          </button>
        </>
      );
    }

    render(
      <TradingModeProvider authenticated={true}>
        <RefreshHarness />
      </TradingModeProvider>,
    );
    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-badge')).toHaveAttribute('data-mode', 'paper');
    });

    screen.getByTestId('force-refresh').click();

    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-badge')).toHaveAttribute('data-mode', 'live');
    });
  });
});
