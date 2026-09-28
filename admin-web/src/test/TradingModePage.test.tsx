/**
 * Trading Mode Master Switch — frontend integration tests.
 *
 * The backend remains the source of truth.  These tests prove that:
 *   • GET /api/v1/trading/mode drives the initial UI state.
 *   • POST /api/v1/trading/mode is fired with the correct payload,
 *     Bearer token, LIVE confirmation phrase, and reason.
 *   • The UI always mirrors the server response — never optimistic.
 *   • Known backend error codes are surfaced clearly.
 *   • A failed POST does not silently flip the display.
 *   • The persistent context is refreshed after every successful change.
 *   • Auto-revert configuration is persisted through GET/POST /trading/mode/auto-revert.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import TradingModePage from '../pages/TradingModePage';
import { tokenStore } from '../api';
import { TradingModeProvider } from '../TradingModeContext';

const CONFIRMATION = 'I UNDERSTAND THIS CAN PLACE REAL-MONEY ORDERS.';

function paperSnapshot(overrides: Record<string, unknown> = {}) {
  return {
    mode: 'paper',
    changed_at: null,
    changed_by: null,
    armed_at: null,
    cooldown_active: false,
    cooldown_expires_at: null,
    cooldown_seconds: 0,
    active_live_sessions: 0,
    open_live_positions: 0,
    open_live_orders: 0,
    kill_switch_active: false,
    live_gate: 'requires_existing_live_safety_checks',
    auto_revert: {
      enabled: false, at_time: null, timezone: null,
      last_run_date: null, last_run_result: null, last_run_at: null,
    },
    ...overrides,
  };
}

function liveSnapshot(overrides: Record<string, unknown> = {}) {
  return paperSnapshot({
    mode: 'live',
    changed_at: '2026-02-05T10:00:00Z',
    changed_by: 'admin-user-id',
    armed_at: '2026-02-05T10:00:00Z',
    ...overrides,
  });
}

function autoRevertPayload(overrides: Record<string, unknown> = {}) {
  return {
    enabled: false, at_time: null, timezone: null,
    last_run_date: null, last_run_result: null, last_run_at: null,
    ...overrides,
  };
}

/** Build a Response-like object the api.ts `request` helper expects. */
function jsonResponse(body: unknown, init: { ok?: boolean; status?: number } = {}) {
  const ok = init.ok ?? true;
  const status = init.status ?? 200;
  return {
    ok,
    status,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
}

/**
 * URL-routing fetch stub — every test wires a table of URL/method → response
 * so the exact call order (context prefetch, page load, auto-revert load,
 * POST, reload) doesn't matter.
 */
type Route = {
  match: (url: string, init: RequestInit) => boolean;
  respond: (call: number) => Response | Promise<Response>;
};

function installRouter(routes: Route[]): any {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  const counters = new Map<Route, number>();
  const stub = vi.fn(async (url: string, init: RequestInit = {}) => {
    calls.push({ url, init });
    for (const r of routes) {
      if (r.match(url, init)) {
        const n = counters.get(r) ?? 0;
        counters.set(r, n + 1);
        return r.respond(n);
      }
    }
    throw new Error(`No route for ${(init.method || 'GET').toUpperCase()} ${url}`);
  });
  vi.stubGlobal('fetch', stub as unknown as typeof fetch);
  (stub as any).calls = calls;
  return stub;
}

function isMethod(init: RequestInit, method: string): boolean {
  return (init.method || 'GET').toUpperCase() === method.toUpperCase();
}

function renderPage() {
  return render(
    <TradingModeProvider authenticated={true}>
      <TradingModePage />
    </TradingModeProvider>,
  );
}

beforeEach(() => {
  tokenStore.set('test-access-token');
});

describe('TradingModePage — GET integration', () => {
  it('loads and displays PAPER when backend returns PAPER', async () => {
    const stub = installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(
        /PAPER TRADING — No real-money orders/i,
      );
    });

    // Authorization header was attached to every GET.
    for (const c of stub.calls) {
      expect(
        (c.init.headers as Record<string, string> | undefined)?.Authorization,
      ).toBe('Bearer test-access-token');
    }
    expect(screen.getByTestId('trading-mode-live-button')).not.toBeDisabled();
    expect(screen.getByTestId('trading-mode-paper-button')).toBeDisabled();
  });

  it('loads and displays LIVE when backend returns LIVE', async () => {
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(liveSnapshot({ active_live_sessions: 2 })),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
    ]);

    renderPage();

    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(
        /LIVE TRADING — Real-money execution enabled/i,
      );
    });
    expect(screen.getByTestId('trading-mode-live-sessions')).toHaveTextContent('2');
    expect(screen.getByTestId('trading-mode-paper-button')).not.toBeDisabled();
    expect(screen.getByTestId('trading-mode-live-button')).toBeDisabled();
  });

  it('surfaces cooldown state distinctly when the server reports cooldown_active', async () => {
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(liveSnapshot({
          cooldown_active: true,
          cooldown_expires_at: '2026-02-05T10:01:00Z',
          cooldown_seconds: 60,
          live_gate: 'cooldown_active',
        })),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
    ]);

    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(
        /LIVE TRADING · COOLDOWN/i,
      );
    });
  });

  it('shows an error when the initial GET fails', async () => {
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse({ detail: 'boom' }, { ok: false, status: 500 }),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
    ]);
    renderPage();
    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-error')).toBeInTheDocument();
    });
  });
});

describe('TradingModePage — POST PAPER → LIVE', () => {
  it('sends POST with confirmation and updates from server', async () => {
    const user = userEvent.setup();

    let currentMode: any = paperSnapshot();
    const stub = installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(currentMode),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => {
          currentMode = liveSnapshot();
          return jsonResponse(currentMode);
        },
      },
    ]);

    renderPage();
    await screen.findByTestId('trading-mode-current');

    await user.type(screen.getByTestId('trading-mode-reason'), 'go-live drill');
    await user.click(screen.getByTestId('trading-mode-live-button'));
    const modal = await screen.findByTestId('trading-mode-live-confirmation');
    const confirmBtn = within(modal).getByTestId('trading-mode-live-confirm-button');
    expect(confirmBtn).toBeDisabled();
    await user.click(within(modal).getByTestId('trading-mode-live-ack'));
    expect(confirmBtn).toBeDisabled();
    await user.type(within(modal).getByTestId('trading-mode-live-phrase'), CONFIRMATION);
    expect(confirmBtn).not.toBeDisabled();

    await user.click(confirmBtn);
    await waitFor(() => {
      expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/LIVE TRADING/i);
    });

    const post = stub.calls.find((c: any) => (c.init.method || '').toUpperCase() === 'POST' && c.url === '/api/v1/trading/mode');
    expect(post).toBeTruthy();
    const body = JSON.parse(String(post!.init.body));
    expect(body).toEqual({ mode: 'live', reason: 'go-live drill', confirmation: CONFIRMATION });
    expect((post!.init.headers as Record<string, string>).Authorization).toBe('Bearer test-access-token');
  });

  it('surfaces live_confirmation_required and keeps UI on PAPER', async () => {
    const user = userEvent.setup();
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => jsonResponse({
          detail: { message: 'Explicit real-money trading confirmation is required',
                    code: 'live_confirmation_required' },
        }, { ok: false, status: 400 }),
      },
    ]);

    renderPage();
    await screen.findByTestId('trading-mode-current');
    await user.click(screen.getByTestId('trading-mode-live-button'));
    await user.click(screen.getByTestId('trading-mode-live-ack'));
    await user.type(screen.getByTestId('trading-mode-live-phrase'), CONFIRMATION);
    await user.click(screen.getByTestId('trading-mode-live-confirm-button'));

    await waitFor(() =>
      expect(screen.getByTestId('toast')).toHaveTextContent(/exact confirmation phrase/i),
    );
    expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/PAPER TRADING/i);
  });

  it('surfaces live_cooldown_active error when the server reports the cooldown gate', async () => {
    const user = userEvent.setup();
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => jsonResponse({
          detail: { message: 'LIVE cooldown active', code: 'live_cooldown_active' },
        }, { ok: false, status: 400 }),
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');
    await user.click(screen.getByTestId('trading-mode-live-button'));
    await user.click(screen.getByTestId('trading-mode-live-ack'));
    await user.type(screen.getByTestId('trading-mode-live-phrase'), CONFIRMATION);
    await user.click(screen.getByTestId('trading-mode-live-confirm-button'));

    await waitFor(() =>
      expect(screen.getByTestId('toast')).toHaveTextContent(/cooldown/i),
    );
    expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/PAPER TRADING/i);
  });

  it('surfaces 401 unauthorized without changing UI state', async () => {
    Object.defineProperty(window, 'location', {
      writable: true,
      value: { href: '', assign: vi.fn() } as unknown as Location,
    });
    const user = userEvent.setup();
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => jsonResponse({
          detail: { message: 'not admin', code: 'forbidden' },
        }, { ok: false, status: 401 }),
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');
    await user.click(screen.getByTestId('trading-mode-live-button'));
    await user.click(screen.getByTestId('trading-mode-live-ack'));
    await user.type(screen.getByTestId('trading-mode-live-phrase'), CONFIRMATION);
    await user.click(screen.getByTestId('trading-mode-live-confirm-button'));
    await waitFor(() =>
      expect(screen.getByTestId('toast')).toHaveTextContent(/Admin authorization required/i),
    );
    expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/PAPER TRADING/i);
  });
});

describe('TradingModePage — POST LIVE → PAPER', () => {
  it('sends POST without confirmation and updates UI from server', async () => {
    const user = userEvent.setup();
    let currentMode: any = liveSnapshot();
    const stub = installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(currentMode),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => {
          currentMode = paperSnapshot({ changed_by: 'admin-user-id' });
          return jsonResponse(currentMode);
        },
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');

    await user.click(screen.getByTestId('trading-mode-paper-button'));
    await waitFor(() =>
      expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/PAPER TRADING/i),
    );
    const post = stub.calls.find((c: any) => (c.init.method || '').toUpperCase() === 'POST');
    const body = JSON.parse(String(post!.init.body));
    expect(body.mode).toBe('paper');
    expect(body.confirmation).toBeUndefined();
  });

  it('surfaces live_exposure_blocks_paper_mode and keeps UI on LIVE', async () => {
    const user = userEvent.setup();
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(liveSnapshot({ active_live_sessions: 1, open_live_positions: 2 })),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'POST'),
        respond: () => jsonResponse({
          detail: {
            message: 'blocked',
            code: 'live_exposure_blocks_paper_mode',
            details: { active_live_sessions: 1, open_live_positions: 2, open_live_orders: 0 },
          },
        }, { ok: false, status: 409 }),
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');
    await user.click(screen.getByTestId('trading-mode-paper-button'));
    await waitFor(() =>
      expect(screen.getByTestId('toast')).toHaveTextContent(/LIVE exposure remains/i),
    );
    expect(screen.getByTestId('toast')).toHaveTextContent(/sessions: 1/);
    expect(screen.getByTestId('toast')).toHaveTextContent(/positions: 2/);
    expect(screen.getByTestId('trading-mode-current')).toHaveTextContent(/LIVE TRADING/i);
  });
});

describe('TradingModePage — auto-revert configuration', () => {
  it('loads the persisted config and POSTs updates', async () => {
    const user = userEvent.setup();
    let persisted = autoRevertPayload({ enabled: false });
    const stub = installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(persisted),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'POST'),
        respond: () => {
          persisted = autoRevertPayload({
            enabled: true, at_time: '15:30', timezone: 'Asia/Kolkata',
          });
          return jsonResponse(persisted);
        },
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');

    await user.click(screen.getByTestId('auto-revert-enabled'));
    await user.click(screen.getByTestId('auto-revert-save'));

    await waitFor(() => {
      expect(screen.getByTestId('toast')).toHaveTextContent(/Auto-revert saved/i);
    });
    const post = stub.calls.find((c: any) =>
      (c.init.method || '').toUpperCase() === 'POST' &&
      c.url === '/api/v1/trading/mode/auto-revert',
    );
    expect(post).toBeTruthy();
    const body = JSON.parse(String(post!.init.body));
    expect(body.enabled).toBe(true);
    expect(body.at_time).toBe('15:30');
    expect(body.timezone).toBe('Asia/Kolkata');
  });

  it('surfaces invalid_auto_revert_config from the server', async () => {
    const user = userEvent.setup();
    installRouter([
      {
        match: (u, i) => u === '/api/v1/trading/mode' && isMethod(i, 'GET'),
        respond: () => jsonResponse(paperSnapshot()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'GET'),
        respond: () => jsonResponse(autoRevertPayload()),
      },
      {
        match: (u, i) => u === '/api/v1/trading/mode/auto-revert' && isMethod(i, 'POST'),
        respond: () => jsonResponse({
          detail: { message: 'bad', code: 'invalid_auto_revert_config' },
        }, { ok: false, status: 400 }),
      },
    ]);
    renderPage();
    await screen.findByTestId('trading-mode-current');
    await user.click(screen.getByTestId('auto-revert-save'));
    await waitFor(() =>
      expect(screen.getByTestId('toast')).toHaveTextContent(/Auto-revert config is invalid/i),
    );
  });
});
