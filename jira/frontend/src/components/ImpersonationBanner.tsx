import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { apiErrorMessage } from '../api/client';

export function ImpersonationBanner() {
  const impersonating = useAuth((s) => s.impersonating);
  const user = useAuth((s) => s.user);
  const stopImpersonation = useAuth((s) => s.stopImpersonation);
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!impersonating) return null;

  async function onReturn() {
    setBusy(true);
    setError(null);
    try {
      await stopImpersonation();
      navigate('/');
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not return to your account'));
      setBusy(false);
    }
  }

  return (
    <div className="impersonation-banner" role="alert">
      <span className="impersonation-eye" aria-hidden="true">
        👁
      </span>
      <span className="impersonation-text">
        Viewing as <strong>{user?.display_name ?? 'another user'}</strong>
        {user?.email ? <span className="impersonation-email"> ({user.email})</span> : null}
        {' — '}you’re seeing their view and permissions.
        {error ? <span className="impersonation-error"> {error}</span> : null}
      </span>
      <button className="impersonation-return" onClick={onReturn} disabled={busy}>
        {busy ? 'Returning…' : 'Return to your account'}
      </button>
    </div>
  );
}
