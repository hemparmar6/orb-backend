import { useEffect, useState } from 'react';
import { NavLink, Navigate, Outlet, Route, Routes, useLocation } from 'react-router-dom';
import { AuthProvider, useAuth } from './auth';
import { TradingModeProvider } from './TradingModeContext';
import { TradingModeBadge } from './TradingModeBadge';
import LoginPage from './pages/LoginPage';
import DashboardPage from './pages/DashboardPage';
import UsersPage from './pages/UsersPage';
import BrokersPage from './pages/BrokersPage';
import StrategiesPage from './pages/StrategiesPage';
import BacktestsPage from './pages/BacktestsPage';
import SessionsPage from './pages/SessionsPage';
import OrdersPage from './pages/OrdersPage';
import TradesPage from './pages/TradesPage';
import AuditLogsPage from './pages/AuditLogsPage';
import SystemPage from './pages/SystemPage';
import PortfolioPage from './pages/PortfolioPage';
import RiskPage from './pages/RiskPage';
import NotificationsPage from './pages/NotificationsPage';
import ReportsPage from './pages/ReportsPage';
import SchedulerPage from './pages/SchedulerPage';
import SubscriptionsPage from './pages/SubscriptionsPage';
// Module 9 - AI Trading Intelligence
import AiAnalyticsPage from './pages/AiAnalyticsPage';
import OptimisationJobsPage from './pages/OptimisationJobsPage';
import RecommendationCentrePage from './pages/RecommendationCentrePage';
import MarketIntelligencePage from './pages/MarketIntelligencePage';
import StrategyPerformancePage from './pages/StrategyPerformancePage';
// Module 10 - Enterprise & Production
import HealthDashboardPage from './pages/monitoring/HealthDashboardPage';
import MonitoringPage from './pages/monitoring/MonitoringPage';
import LogsPage from './pages/monitoring/LogsPage';
import PerformancePage from './pages/monitoring/PerformancePage';
import SecurityPage from './pages/monitoring/SecurityPage';
import BackupsPage from './pages/monitoring/BackupsPage';
// v1.1.0 Phase 4/5 — Bot Management + Circuit Breakers + Kill Switch
import CircuitBreakersPage from './pages/CircuitBreakersPage';
import KillSwitchHistoryPage from './pages/KillSwitchHistoryPage';
// Milestone 9 — Risk Management + Milestone 8 — Execution Safety admin
import RiskManagementPage from './pages/RiskManagementPage';
import ExecutionSafetyPage from './pages/ExecutionSafetyPage';
import TradingModePage from './pages/TradingModePage';
import CreatorsPage from './pages/CreatorsPage';

const NAV = [
  { to: '/', label: 'Overview', end: true, section: 'DASHBOARD', testId: 'nav-dashboard' },
  { to: '/portfolio', label: 'Portfolio', section: 'DASHBOARD', testId: 'nav-portfolio' },
  { to: '/risk', label: 'Risk', section: 'DASHBOARD', testId: 'nav-risk' },
  { to: '/users', label: 'Users', section: 'MANAGE', testId: 'nav-users' },
  { to: '/brokers', label: 'Broker Accounts', section: 'MANAGE', testId: 'nav-brokers' },
  { to: '/strategies', label: 'Strategies', section: 'MANAGE', testId: 'nav-strategies' },
  { to: '/sessions', label: 'Trading Sessions', section: 'TRADING', testId: 'nav-sessions' },
  { to: '/orders', label: 'Orders', section: 'TRADING', testId: 'nav-orders' },
  { to: '/trades', label: 'Trades / Fills', section: 'TRADING', testId: 'nav-trades' },
  { to: '/backtests', label: 'Backtests', section: 'TRADING', testId: 'nav-backtests' },
  { to: '/notifications', label: 'Notifications', section: 'COMMS', testId: 'nav-notifications' },
  { to: '/reports', label: 'Reports', section: 'COMMS', testId: 'nav-reports' },
  { to: '/scheduler', label: 'Scheduler', section: 'COMMS', testId: 'nav-scheduler' },
  { to: '/subscriptions', label: 'Subscriptions', section: 'BILLING', testId: 'nav-subscriptions' },
  { to: '/creators', label: 'Creators', section: 'BILLING', testId: 'nav-creators' },
  // Module 9 - AI Trading Intelligence
  { to: '/ai/analytics', label: 'AI Analytics', section: 'AI', testId: 'nav-ai-analytics' },
  { to: '/ai/optimisation', label: 'Optimisation Jobs', section: 'AI', testId: 'nav-ai-optim' },
  { to: '/ai/strategy-performance', label: 'Strategy Performance', section: 'AI', testId: 'nav-ai-strategy' },
  { to: '/ai/recommendations', label: 'Recommendation Centre', section: 'AI', testId: 'nav-ai-recs' },
  { to: '/ai/market-intelligence', label: 'Market Intelligence', section: 'AI', testId: 'nav-ai-mi' },
  { to: '/audit-logs', label: 'Audit Log', section: 'GOVERNANCE', testId: 'nav-audit' },
  { to: '/system', label: 'System Health (legacy)', section: 'GOVERNANCE', testId: 'nav-system' },
  // Module 10 — Enterprise & Production
  { to: '/monitoring/health', label: 'Health Dashboard', section: 'OPERATIONS', testId: 'nav-mon-health' },
  { to: '/monitoring/metrics', label: 'Monitoring', section: 'OPERATIONS', testId: 'nav-mon-metrics' },
  { to: '/monitoring/logs', label: 'Logs', section: 'OPERATIONS', testId: 'nav-mon-logs' },
  { to: '/monitoring/performance', label: 'Performance', section: 'OPERATIONS', testId: 'nav-mon-perf' },
  { to: '/monitoring/security', label: 'Security', section: 'OPERATIONS', testId: 'nav-mon-security' },
  { to: '/monitoring/backups', label: 'Backups', section: 'OPERATIONS', testId: 'nav-mon-backups' },
  // v1.1.0 Phase 4/5 — Automation & Safety
  { to: '/automation/circuit-breakers', label: 'Circuit Breakers', section: 'AUTOMATION', testId: 'nav-cb' },
  { to: '/automation/kill-switch', label: 'Kill Switch History', section: 'AUTOMATION', testId: 'nav-ks' },
  // Milestone 8 & 9 — Safety & Risk
  { to: '/safety/execution', label: 'Execution Safety', section: 'AUTOMATION', testId: 'nav-exec-safety' },
  { to: '/safety/trading-mode', label: 'Trading Mode', section: 'AUTOMATION', testId: 'nav-trading-mode' },
  { to: '/safety/risk', label: 'Risk Management', section: 'AUTOMATION', testId: 'nav-risk-mgmt' },
];

function Shell() {
  const { user, logout } = useAuth();
  const loc = useLocation();
  const [now, setNow] = useState(new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(t);
  }, []);

  const grouped: Record<string, typeof NAV> = {};
  NAV.forEach(n => { (grouped[n.section] ||= []).push(n); });

  const currentTitle = NAV.find(n => (n.end ? loc.pathname === n.to : loc.pathname.startsWith(n.to)))?.label || 'Admin';

  return (
    <div className="app-shell">
      <aside className="sidebar" data-testid="admin-sidebar">
        <div className="sidebar-logo">
          ORB · AI
          <small>ADMIN CONSOLE</small>
        </div>
        {Object.entries(grouped).map(([section, items]) => (
          <div key={section}>
            <div className="nav-section-label">{section}</div>
            {items.map(n => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end as any}
                data-testid={n.testId}
                className={({ isActive }) => 'nav-item' + (isActive ? ' active' : '')}
              >
                <span className="dot" />
                <span>{n.label}</span>
              </NavLink>
            ))}
          </div>
        ))}
        <div className="sidebar-footer">
          <div className="mono">{user?.email}</div>
          <div className="dim">role: {user?.role}</div>
          <button data-testid="admin-logout-button" onClick={() => logout()}>Sign out</button>
        </div>
      </aside>
      <main className="main">
        <header className="main-header">
          <h1 data-testid="page-title">{currentTitle}</h1>
          <div className="meta">
            <TradingModeBadge testId="global-trading-mode-badge" />
            <span className="status-dot ok" />
            {now.toISOString().replace('T', ' ').slice(0, 19)} UTC
          </div>
        </header>
        <div className="main-body">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

function Protected() {
  const { user, loading } = useAuth();
  if (loading) return <div style={{ padding: 40 }}>Loading…</div>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== 'admin') return <Navigate to="/login" replace />;
  return (
    <TradingModeProvider authenticated={true}>
      <Shell />
    </TradingModeProvider>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <Routes>
        <Route path="/login" element={<LoginPage />} />
        <Route element={<Protected />}>
          <Route index element={<DashboardPage />} />
          <Route path="/portfolio" element={<PortfolioPage />} />
          <Route path="/risk" element={<RiskPage />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/brokers" element={<BrokersPage />} />
          <Route path="/strategies" element={<StrategiesPage />} />
          <Route path="/sessions" element={<SessionsPage />} />
          <Route path="/orders" element={<OrdersPage />} />
          <Route path="/trades" element={<TradesPage />} />
          <Route path="/backtests" element={<BacktestsPage />} />
          <Route path="/notifications" element={<NotificationsPage />} />
          <Route path="/reports" element={<ReportsPage />} />
          <Route path="/scheduler" element={<SchedulerPage />} />
          <Route path="/subscriptions" element={<SubscriptionsPage />} />
          <Route path="/creators" element={<CreatorsPage />} />
          {/* Module 9 - AI Trading Intelligence */}
          <Route path="/ai/analytics" element={<AiAnalyticsPage />} />
          <Route path="/ai/optimisation" element={<OptimisationJobsPage />} />
          <Route path="/ai/strategy-performance" element={<StrategyPerformancePage />} />
          <Route path="/ai/recommendations" element={<RecommendationCentrePage />} />
          <Route path="/ai/market-intelligence" element={<MarketIntelligencePage />} />
          <Route path="/audit-logs" element={<AuditLogsPage />} />
          <Route path="/system" element={<SystemPage />} />
          {/* Module 10 — Enterprise & Production */}
          <Route path="/monitoring/health" element={<HealthDashboardPage />} />
          <Route path="/monitoring/metrics" element={<MonitoringPage />} />
          <Route path="/monitoring/logs" element={<LogsPage />} />
          <Route path="/monitoring/performance" element={<PerformancePage />} />
          <Route path="/monitoring/security" element={<SecurityPage />} />
          <Route path="/monitoring/backups" element={<BackupsPage />} />
          {/* v1.1.0 Phase 4/5 — Automation & Safety */}
          <Route path="/automation/circuit-breakers" element={<CircuitBreakersPage />} />
          <Route path="/automation/kill-switch" element={<KillSwitchHistoryPage />} />
          {/* Milestone 8 & 9 — Execution Safety + Risk Management */}
          <Route path="/safety/execution" element={<ExecutionSafetyPage />} />
          <Route path="/safety/trading-mode" element={<TradingModePage />} />
          <Route path="/safety/risk" element={<RiskManagementPage />} />
        </Route>
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </AuthProvider>
  );
}
