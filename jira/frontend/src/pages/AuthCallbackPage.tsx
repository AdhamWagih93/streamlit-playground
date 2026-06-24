import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { SpinnerCenter } from '../components/Spinner';

export function AuthCallbackPage() {
  const setTokens = useAuth((s) => s.setTokens);
  const navigate = useNavigate();
  const [error, setError] = useState('');

  useEffect(() => {
    // Entra callback redirects to /auth/callback#access_token=..&refresh_token=..
    // (or #error=..). Parse the hash fragment.
    const hash = window.location.hash.startsWith('#') ? window.location.hash.slice(1) : window.location.hash;
    const params = new URLSearchParams(hash);
    const err = params.get('error');
    if (err) {
      setError(err);
      return;
    }
    const access = params.get('access_token');
    const refresh = params.get('refresh_token');
    if (!access || !refresh) {
      setError('Missing tokens in callback.');
      return;
    }
    setTokens(access, refresh)
      .then(() => navigate('/projects', { replace: true }))
      .catch(() => setError('Could not complete sign-in.'));
  }, [setTokens, navigate]);

  if (error) {
    return (
      <div className="auth-wrap">
        <div className="auth-card">
          <div className="auth-title">Sign-in failed</div>
          <div className="alert alert-error">{error}</div>
          <button className="btn btn-primary btn-block" onClick={() => navigate('/login', { replace: true })}>
            Back to sign in
          </button>
        </div>
      </div>
    );
  }

  return <SpinnerCenter />;
}
