import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { register } from '../api/auth';
import { getAuthPolicy } from '../api/authProviders';
import { useAuth } from '../store/auth';
import { apiErrorMessage } from '../api/client';

export function RegisterPage() {
  const navigate = useNavigate();
  const login = useAuth((s) => s.login);
  const [form, setForm] = useState({ username: '', email: '', display_name: '', password: '' });
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [allowSelfRegistration, setAllowSelfRegistration] = useState(true);

  useEffect(() => {
    getAuthPolicy()
      .then((p) => setAllowSelfRegistration(p.allow_self_registration))
      .catch(() => setAllowSelfRegistration(true));
  }, []);

  function set(k: keyof typeof form, v: string) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (!form.email || !form.password) return setError('Email and password are required');
    setBusy(true);
    try {
      await register({
        username: form.username || form.email,
        email: form.email,
        display_name: form.display_name || form.email.split('@')[0],
        password: form.password,
      });
      await login(form.email, form.password);
      navigate('/projects', { replace: true });
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not create account'));
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
        <div className="auth-title">Create your account</div>
        <div className="auth-sub">Start tracking work in minutes</div>

        {error && <div className="alert alert-error">{error}</div>}

        {!allowSelfRegistration ? (
          <>
            <div className="alert alert-error">Self-registration is disabled by your administrator.</div>
            <div className="auth-foot">
              Already have an account? <Link to="/login">Sign in</Link>
            </div>
          </>
        ) : (
          <>
        <div className="field">
          <label>Display name</label>
          <input className="input" value={form.display_name} onChange={(e) => set('display_name', e.target.value)} placeholder="Jane Doe" />
        </div>
        <div className="field">
          <label>Username</label>
          <input className="input" value={form.username} onChange={(e) => set('username', e.target.value)} placeholder="jdoe" />
        </div>
        <div className="field">
          <label>Email</label>
          <input className="input" type="email" value={form.email} onChange={(e) => set('email', e.target.value)} placeholder="you@company.com" />
        </div>
        <div className="field">
          <label>Password</label>
          <input className="input" type="password" value={form.password} onChange={(e) => set('password', e.target.value)} placeholder="At least 8 characters" />
        </div>
        <button className="btn btn-primary btn-block" type="submit" disabled={busy}>
          {busy ? 'Creating…' : 'Create account'}
        </button>
        <div className="auth-foot">
          Already have an account? <Link to="/login">Sign in</Link>
        </div>
          </>
        )}
      </form>
    </div>
  );
}
