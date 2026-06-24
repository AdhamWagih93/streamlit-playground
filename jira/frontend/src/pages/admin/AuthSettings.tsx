import { useEffect, useState } from 'react';
import { AuthSettings as AuthSettingsType } from '../../types';
import { getAuthSettings, updateAuthSettings } from '../../api/admin';
import { SpinnerCenter } from '../../components/Spinner';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function AuthSettings() {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastMsg | null>(null);

  // form fields
  const [allowLocalLogin, setAllowLocalLogin] = useState(true);
  const [allowSelfRegistration, setAllowSelfRegistration] = useState(true);
  const [accessTokenMinutes, setAccessTokenMinutes] = useState('');
  const [refreshTokenMinutes, setRefreshTokenMinutes] = useState('');
  const [allowedDomains, setAllowedDomains] = useState('');

  function apply(s: AuthSettingsType) {
    setAllowLocalLogin(s.allow_local_login);
    setAllowSelfRegistration(s.allow_self_registration);
    setAccessTokenMinutes(s.access_token_minutes == null ? '' : String(s.access_token_minutes));
    setRefreshTokenMinutes(s.refresh_token_minutes == null ? '' : String(s.refresh_token_minutes));
    setAllowedDomains(s.registration_allowed_domains ?? '');
  }

  useEffect(() => {
    getAuthSettings()
      .then(apply)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  function toNumberOrNull(v: string): number | null {
    const t = v.trim();
    if (t === '') return null;
    const n = Number(t);
    return Number.isFinite(n) ? n : null;
  }

  async function save() {
    setBusy(true);
    setError('');
    try {
      const domains = allowedDomains.trim();
      const updated = await updateAuthSettings({
        allow_local_login: allowLocalLogin,
        allow_self_registration: allowSelfRegistration,
        access_token_minutes: toNumberOrNull(accessTokenMinutes),
        refresh_token_minutes: toNumberOrNull(refreshTokenMinutes),
        registration_allowed_domains: domains === '' ? null : domains,
      });
      apply(updated);
      setToast({ ok: true, text: 'Authentication settings saved' });
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save authentication settings'));
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 className="page-title" style={{ fontSize: 19, marginBottom: 4 }}>Authentication</h2>
      <p className="muted text-sm mb-16">Control how users sign in and register on this instance.</p>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="toggle-row">
        <div>
          <div style={{ fontWeight: 600 }}>Allow local password login</div>
          <div className="muted text-xs">When off, users can only sign in through configured identity providers.</div>
        </div>
        <label className="row gap-8">
          <input type="checkbox" checked={allowLocalLogin} onChange={(e) => setAllowLocalLogin(e.target.checked)} />
        </label>
      </div>

      <div className="toggle-row">
        <div>
          <div style={{ fontWeight: 600 }}>Allow self-registration</div>
          <div className="muted text-xs">When off, new users cannot create their own accounts.</div>
        </div>
        <label className="row gap-8">
          <input
            type="checkbox"
            checked={allowSelfRegistration}
            onChange={(e) => setAllowSelfRegistration(e.target.checked)}
          />
        </label>
      </div>

      <div className="field-grid mt-16">
        <div className="field">
          <label>Access token lifetime (minutes)</label>
          <input
            className="input"
            type="number"
            value={accessTokenMinutes}
            onChange={(e) => setAccessTokenMinutes(e.target.value)}
            placeholder="Default"
          />
        </div>
        <div className="field">
          <label>Refresh token lifetime (minutes)</label>
          <input
            className="input"
            type="number"
            value={refreshTokenMinutes}
            onChange={(e) => setRefreshTokenMinutes(e.target.value)}
            placeholder="Default"
          />
        </div>
      </div>

      <div className="field">
        <label>Self-registration allowed email domains</label>
        <textarea
          className="input"
          rows={2}
          value={allowedDomains}
          onChange={(e) => setAllowedDomains(e.target.value)}
          placeholder="acme.com, contoso.com"
        />
        <div className="muted text-xs mt-8">Leave blank to allow any domain. e.g. acme.com, contoso.com</div>
      </div>

      <button className="btn btn-primary mt-16" onClick={save} disabled={busy}>
        {busy ? 'Saving…' : 'Save changes'}
      </button>

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
