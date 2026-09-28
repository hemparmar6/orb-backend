import { FormEvent, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../auth';

export default function LoginPage() {
  const [email, setEmail] = useState('admin@orb.com');
  const [password, setPassword] = useState('Admin@123');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const { login, user } = useAuth();
  const nav = useNavigate();

  if (user && user.role === 'admin') { nav('/', { replace: true }); }

  async function onSubmit(e: FormEvent) {
    e.preventDefault();
    setErr(null); setBusy(true);
    try {
      await login(email.trim(), password);
      nav('/', { replace: true });
    } catch (e: any) {
      setErr(e?.message || 'Login failed');
    } finally { setBusy(false); }
  }

  return (
    <div className="login-shell">
      <form className="login-card" onSubmit={onSubmit} data-testid="login-form">
        <div className="login-title">ORB · AI</div>
        <div className="login-sub">Admin Console</div>

        <label>Email</label>
        <input
          data-testid="login-email-input"
          type="email"
          autoComplete="username"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          required
        />
        <label>Password</label>
        <input
          data-testid="login-password-input"
          type="password"
          autoComplete="current-password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          required
        />
        {err && <div className="err" data-testid="login-error">{err}</div>}
        <button data-testid="login-submit-button" disabled={busy}>
          {busy ? 'Signing in…' : 'SIGN IN'}
        </button>
        <div className="login-hint">
          Default dev credentials:<br />
          <code>admin@orb.com</code> / <code>Admin@123</code><br />
          <span className="dim">Change the password after first login.</span>
        </div>
      </form>
    </div>
  );
}
