import { useEffect, useState } from 'react';
import { Link, useNavigate, useLocation } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { apiErrorMessage } from '../api/client';
import { AuthProvider } from '../types';
import { listAuthProviders, getEntraAuthorizeUrl, getAuthPolicy } from '../api/authProviders';

export function LoginPage() {
  const login = useAuth((s) => s.login);
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<AuthProvider[]>([]);
  const [allowLocalLogin, setAllowLocalLogin] = useState(true);
  const [allowSelfRegistration, setAllowSelfRegistration] = useState(true);

  const from = (location.state as { from?: string })?.from || '/projects';

  useEffect(() => {
    listAuthProviders()
      .then((p) => setProviders(p.filter((x) => x.enabled)))
      .catch(() => setProviders([]));
    getAuthPolicy()
      .then((p) => {
        setAllowLocalLogin(p.allow_local_login);
        setAllowSelfRegistration(p.allow_self_registration);
      })
      .catch(() => {
        setAllowLocalLogin(true);
        setAllowSelfRegistration(true);
      });
  }, []);

  const entraProviders = providers.filter((p) => p.type === 'entra');

  async function signInWithEntra(id: string) {
    setError('');
    try {
      const url = await getEntraAuthorizeUrl(id);
      window.location.href = url;
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not start Microsoft sign-in'));
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setBusy(true);
    try {
      await login(email, password);
      navigate(from, { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err, 'Invalid email or password'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="auth-wrap">
      <form className="auth-card" onSubmit={submit}>
        <div className="auth-brand">
          <span className="brand-mark">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
              <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </span>
          <span style={{ fontSize: 19, fontWeight: 700 }}>Trackly</span>
        </div>
        <div className="auth-title">Welcome back</div>
        <div className="auth-sub">Sign in to your workspace</div>

        {error && <div className="alert alert-error">{error}</div>}

        {allowLocalLogin && (
          <>
            <div className="field">
              <label>Email</label>
              <input
                className="input"
                type="email"
                autoFocus
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="you@company.com"
              />
            </div>
            <div className="field">
              <label>Password</label>
              <input
                className="input"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="••••••••"
              />
            </div>
            <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
              {busy ? 'Signing in…' : 'Sign in'}
            </button>
          </>
        )}

        {!allowLocalLogin && (
          <p className="muted text-sm" style={{ marginBottom: 8 }}>
            Password login is disabled; sign in with your organization account.
          </p>
        )}

        {entraProviders.length > 0 && (
          <>
            {allowLocalLogin && (
              <div className="row gap-8 mt-16" style={{ color: 'var(--text-muted)', fontSize: 12 }}>
                <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
                or
                <span style={{ flex: 1, height: 1, background: 'var(--border)' }} />
              </div>
            )}
            {entraProviders.map((p) => (
              <button key={p.id} type="button" className="btn btn-block mt-8" onClick={() => signInWithEntra(p.id)}>
                Sign in with Microsoft{providers.length > 1 ? ` — ${p.name}` : ''}
              </button>
            ))}
          </>
        )}

        {allowSelfRegistration && (
          <div className="auth-foot">
            No account? <Link to="/register">Create one</Link>
          </div>
        )}
      </form>
    </div>
  );
}
