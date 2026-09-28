// Simple typed API client for ORB AI admin dashboard.
// Base is same-origin — the admin bundle is served by FastAPI, so
// relative paths hit the API through the K8s ingress.
const API_BASE = '/api/v1';

const TOKEN_KEY = 'orb_admin_access_token';
const REFRESH_KEY = 'orb_admin_refresh_token';
const USER_KEY = 'orb_admin_user';

export type CurrentUser = {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
  is_active: boolean;
  is_verified: boolean;
};

export const tokenStore = {
  get: () => localStorage.getItem(TOKEN_KEY),
  getRefresh: () => localStorage.getItem(REFRESH_KEY),
  set: (access: string, refresh?: string) => {
    localStorage.setItem(TOKEN_KEY, access);
    if (refresh) localStorage.setItem(REFRESH_KEY, refresh);
  },
  clear: () => {
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(REFRESH_KEY);
    localStorage.removeItem(USER_KEY);
  },
  setUser: (u: CurrentUser) => localStorage.setItem(USER_KEY, JSON.stringify(u)),
  getUser: (): CurrentUser | null => {
    const raw = localStorage.getItem(USER_KEY);
    if (!raw) return null;
    try { return JSON.parse(raw); } catch { return null; }
  },
};

export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: unknown;
  constructor(status: number, msg: string, code?: string, detail?: unknown) {
    super(msg);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...((init.headers as Record<string, string>) || {}),
  };
  const tok = tokenStore.get();
  if (tok) headers['Authorization'] = `Bearer ${tok}`;

  const res = await fetch(API_BASE + path, { ...init, headers });
  const text = await res.text();
  let body: any = null;
  if (text) {
    try { body = JSON.parse(text); } catch { body = text; }
  }
  if (!res.ok) {
    const msg =
      (body && (body.detail?.message || body.detail || body.message)) ||
      `HTTP ${res.status}`;
    const code = body && (body.detail?.code || body.code);
    if (res.status === 401) {
      tokenStore.clear();
      if (!path.startsWith('/auth/')) {
        window.location.href = '/api/admin-ui/login';
      }
    }
    throw new ApiError(res.status, String(msg), code, body);
  }
  return body as T;
}

// Public alias so pages can make ad-hoc calls without extending `api`.
export const apiFetch = request;

export const api = {
  // --- auth ---
  login: (email: string, password: string) =>
    request<{ access_token: string; refresh_token: string; token_type: string; expires_in: number }>(
      '/auth/login',
      { method: 'POST', body: JSON.stringify({ email, password }) },
    ),
  me: () => request<CurrentUser>('/users/me'),
  logout: () => request<{ message: string }>('/auth/logout', { method: 'POST' }),

  // --- admin ---
  systemHealth: () => request<any>('/admin/system/health'),
  listUsers: (params: Record<string, any> = {}) =>
    request<any>(`/admin/users?${qs(params)}`),
  patchUser: (id: string, payload: any) =>
    request<any>(`/admin/users/${id}`, { method: 'PATCH', body: JSON.stringify(payload) }),

  listBrokerAccounts: (params: Record<string, any> = {}) =>
    request<any>(`/admin/broker-accounts?${qs(params)}`),
  deleteBrokerAccount: (id: string) =>
    request<any>(`/admin/broker-accounts/${id}`, { method: 'DELETE' }),

  listSessions: (params: Record<string, any> = {}) =>
    request<any>(`/admin/sessions?${qs(params)}`),
  stopSession: (id: string) =>
    request<any>(`/admin/sessions/${id}/stop`, { method: 'POST' }),

  listStrategies: (params: Record<string, any> = {}) =>
    request<any>(`/admin/strategies?${qs(params)}`),
  listRegisteredStrategies: () =>
    request<any>('/admin/strategies/registered'),

  listBacktests: (params: Record<string, any> = {}) =>
    request<any>(`/admin/backtests?${qs(params)}`),
  listOrders: (params: Record<string, any> = {}) =>
    request<any>(`/admin/orders?${qs(params)}`),
  listTrades: (params: Record<string, any> = {}) =>
    request<any>(`/admin/trades?${qs(params)}`),
  listAuditLogs: (params: Record<string, any> = {}) =>
    request<any>(`/admin/audit-logs?${qs(params)}`),

  brokerCatalog: () => request<any>('/brokers/catalog'),
  publicHealth: () => request<any>('/health'),

  // --- Module 8 ---
  portfolioSummary: () => request<any>('/portfolio/summary'),
  portfolioHoldings: () => request<any>('/portfolio/holdings'),
  portfolioDaily: (days = 30) => request<any>(`/portfolio/performance/daily?days=${days}`),
  analyticsSummary: () => request<any>('/analytics/summary'),
  analyticsEquity: () => request<any>('/analytics/equity-curve'),
  analyticsMonthly: () => request<any>('/analytics/monthly'),
  riskDashboard: () => request<any>('/risk/dashboard'),
  listNotifications: (params: Record<string, any> = {}) =>
    request<any>(`/notifications?${qs(params)}`),
  reportsHistory: () => request<any>('/reports/history'),
  reportPreview: (type: string) => request<any>(`/reports/preview/${type}`),
  reportGenerateUrl: (type: string, format: 'pdf' | 'csv' = 'pdf') =>
    `/api/v1/reports/generate/${type}?format=${format}`,
  schedulerStatus: () => request<any>('/scheduler/status'),
  schedulerTrigger: (job: string) =>
    request<any>(`/scheduler/trigger/${job}`, { method: 'POST' }),

  // --- Module 9: AI Trading Intelligence ---
  aiPortfolio: () => request<any>('/ai/analytics/portfolio'),
  aiWinProbability: (last_n = 100) =>
    request<any>(`/ai/analytics/win-probability?last_n=${last_n}`),
  aiListReviews: (limit = 50) =>
    request<any>(`/ai/trade-review?limit=${limit}`),
  aiListRecommendations: () => request<any>('/ai/recommendations'),
  aiListOptimJobs: () => request<any>('/optimisation/jobs'),
  aiOptimResults: (job_id: string, top = 20) =>
    request<any>(`/optimisation/jobs/${job_id}/results?top=${top}`),
  aiMarketIntel: (symbol: string) =>
    request<any>(`/market-intelligence/${symbol}`),
  aiSnapshotMarketIntel: (symbol: string, timeframe = 'D1') =>
    request<any>(`/market-intelligence/${symbol}/snapshot?timeframe=${timeframe}`, { method: 'POST' }),

  // --- Module 10: Monitoring, Security, Backups ---
  monitoringHealth: () => request<any>('/monitoring/health'),
  monitoringMetrics: () => request<any>('/monitoring/metrics'),
  monitoringPerformance: () => request<any>('/monitoring/performance'),
  monitoringLogs: (params: Record<string, any> = {}) =>
    request<any>(`/monitoring/logs?${qs(params)}`),
  monitoringSecuritySummary: (window_hours = 24) =>
    request<any>(`/monitoring/security/summary?window_hours=${window_hours}`),
  monitoringLoginActivity: (params: Record<string, any> = {}) =>
    request<any>(`/monitoring/security/login-activity?${qs(params)}`),
  monitoringRateLimitEvents: (limit = 100) =>
    request<any>(`/monitoring/security/rate-limits?limit=${limit}`),
  monitoringBackups: () => request<any>('/monitoring/backups'),
  monitoringBackupsRun: () =>
    request<any>('/monitoring/backups/run', { method: 'POST' }),

  // --- v1.1.0 Phase 4/5: Bots, Circuit Breakers, Kill Switch, Broker Health ---
  botsList: (params: Record<string, any> = {}) =>
    request<any[]>(`/bots?${qs(params)}`),
  botsLiveMonitoring: () => request<any>('/bots/live/monitoring'),
  botAnalyticsSummary: () => request<any>('/bot-analytics/summary'),
  automationMonitorTick: () =>
    request<any>('/automation-monitor/tick', { method: 'POST' }),

  cbConfigsList: (params: Record<string, any> = {}) =>
    request<any[]>(`/circuit-breakers/configs?${qs(params)}`),
  cbConfigUpsert: (payload: any) =>
    request<any>('/circuit-breakers/configs', {
      method: 'POST', body: JSON.stringify(payload),
    }),
  cbConfigDelete: (id: string) =>
    request<any>(`/circuit-breakers/configs/${id}`, { method: 'DELETE' }),
  cbEventsList: (params: Record<string, any> = {}) =>
    request<any[]>(`/circuit-breakers/events?${qs(params)}`),

  ksTrigger: (payload: any) =>
    request<any>('/kill-switch/trigger', {
      method: 'POST', body: JSON.stringify(payload),
    }),
  ksEvents: (params: Record<string, any> = {}) =>
    request<any[]>(`/kill-switch/events?${qs(params)}`),
  ksResolve: (id: string) =>
    request<any>(`/kill-switch/${id}/resolve`, { method: 'POST' }),

  brokerHealth: () => request<any>('/monitoring/broker-health'),

  // --- Milestone 9: Risk Management ---
  rmMyLimits: () => request<any>('/risk-management/me/limits'),
  rmUpdateMyLimits: (payload: any) =>
    request<any>('/risk-management/me/limits', {
      method: 'PUT', body: JSON.stringify(payload),
    }),
  rmMyPortfolio: () => request<any>('/risk-management/me/portfolio'),
  rmMyBreaches: (params: Record<string, any> = {}) =>
    request<any[]>(`/risk-management/me/breaches?${qs(params)}`),
  rmPauseAllBots: (reason?: string) =>
    request<any>('/risk-management/me/pause-all-bots', {
      method: 'POST', body: JSON.stringify({ reason }),
    }),
  rmResumeAllBots: () =>
    request<any>('/risk-management/me/resume-all-bots', { method: 'POST' }),
  rmDisableLive: (reason?: string) =>
    request<any>('/risk-management/me/disable-live-trading', {
      method: 'POST', body: JSON.stringify({ reason }),
    }),
  rmReturnToPaper: (reason?: string) =>
    request<any>('/risk-management/me/return-to-paper-trading', {
      method: 'POST', body: JSON.stringify({ reason }),
    }),
  rmEnableLive: () =>
    request<any>('/risk-management/me/enable-live-trading', { method: 'POST' }),
  rmClearPaperForce: () =>
    request<any>('/risk-management/me/clear-paper-mode-force', { method: 'POST' }),

  rmAdminOverview: (since_minutes = 1440) =>
    request<any>(`/risk-management/admin/overview?since_minutes=${since_minutes}`),
  rmAdminGetUserLimits: (user_id: string) =>
    request<any>(`/risk-management/admin/limits/${user_id}`),
  rmAdminSetUserLimits: (user_id: string, payload: any) =>
    request<any>(`/risk-management/admin/limits/${user_id}`, {
      method: 'PUT', body: JSON.stringify(payload),
    }),
  rmAdminListBreaches: (params: Record<string, any> = {}) =>
    request<any[]>(`/risk-management/admin/breaches?${qs(params)}`),
  rmAdminResolveBreach: (breach_id: string) =>
    request<any>(`/risk-management/admin/breaches/${breach_id}/resolve`, { method: 'POST' }),
  rmAdminPrune: (retention_days = 90) =>
    request<any>(`/risk-management/admin/prune?retention_days=${retention_days}`, { method: 'POST' }),

  // --- Milestone 8: Execution Safety ---
  esSettings: () => request<any>('/execution-safety/settings'),
  esUpdateSettings: (payload: any) =>
    request<any>('/execution-safety/settings', {
      method: 'PATCH', body: JSON.stringify(payload),
    }),
  esKillSwitch: (active: boolean, reason: string) =>
    request<any>('/execution-safety/kill-switch', {
      method: 'POST', body: JSON.stringify({ active, reason }),
    }),
  esEvents: (params: Record<string, any> = {}) =>
    request<any[]>(`/execution-safety/events?${qs(params)}`),
  esDashboard: (since_minutes = 60) =>
    request<any>(`/execution-safety/dashboard?since_minutes=${since_minutes}`),
  esConfigAudit: (params: Record<string, any> = {}) =>
    request<any[]>(`/execution-safety/config-audit?${qs(params)}`),
  esPrune: () =>
    request<any>('/execution-safety/prune', { method: 'POST' }),

  // Global PAPER/LIVE operator gate
  tradingModeGet: () => request<any>('/trading/mode'),
  tradingModeSet: (payload: any) => request<any>('/trading/mode', {
    method: 'POST', body: JSON.stringify(payload),
  }),
  tradingModeAutoRevertGet: () => request<any>('/trading/mode/auto-revert'),
  tradingModeAutoRevertSet: (payload: any) => request<any>('/trading/mode/auto-revert', {
    method: 'POST', body: JSON.stringify(payload),
  }),
};

function qs(params: Record<string, any>): string {
  const sp = new URLSearchParams();
  Object.entries(params).forEach(([k, v]) => {
    if (v === undefined || v === null || v === '') return;
    sp.set(k, String(v));
  });
  return sp.toString();
}
