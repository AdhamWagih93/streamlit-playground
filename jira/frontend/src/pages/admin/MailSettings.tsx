import { useEffect, useState } from 'react';
import { MailSettings as MailSettingsType } from '../../types';
import { getMailSettings, updateMailSettings, testMail } from '../../api/admin';
import { SpinnerCenter } from '../../components/Spinner';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function MailSettings() {
  const [settings, setSettings] = useState<MailSettingsType | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<ToastMsg | null>(null);

  // form fields
  const [enabled, setEnabled] = useState(false);
  const [host, setHost] = useState('');
  const [port, setPort] = useState(587);
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [useTls, setUseTls] = useState(true);
  const [useSsl, setUseSsl] = useState(false);
  const [fromAddress, setFromAddress] = useState('');
  const [fromName, setFromName] = useState('');
  const [testTo, setTestTo] = useState('');

  function apply(s: MailSettingsType) {
    setSettings(s);
    setEnabled(s.enabled);
    setHost(s.host);
    setPort(s.port);
    setUsername(s.username);
    setUseTls(s.use_tls);
    setUseSsl(s.use_ssl);
    setFromAddress(s.from_address);
    setFromName(s.from_name);
    setPassword('');
  }

  useEffect(() => {
    getMailSettings()
      .then(apply)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  async function save() {
    setBusy(true);
    setError('');
    try {
      const updated = await updateMailSettings({
        enabled,
        host,
        port,
        username,
        use_tls: useTls,
        use_ssl: useSsl,
        from_address: fromAddress,
        from_name: fromName,
        password: password || undefined,
      });
      apply(updated);
      setToast({ ok: true, text: 'Mail settings saved' });
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save mail settings'));
    } finally {
      setBusy(false);
    }
  }

  async function sendTest() {
    if (!testTo.trim()) return;
    setBusy(true);
    try {
      const res = await testMail(testTo.trim());
      setToast({ ok: res.ok, text: res.message });
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Test email failed') });
    } finally {
      setBusy(false);
    }
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 640 }}>
      <h2 className="page-title" style={{ fontSize: 19, marginBottom: 4 }}>General / Mail</h2>
      <p className="muted text-sm mb-16">Configure the SMTP server used to send email notifications.</p>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="toggle-row">
        <div>
          <div style={{ fontWeight: 600 }}>Email enabled</div>
          <div className="muted text-xs">When off, no email notifications are sent.</div>
        </div>
        <label className="row gap-8">
          <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
        </label>
      </div>

      <div className="field-grid mt-16">
        <div className="field">
          <label>SMTP host</label>
          <input className="input" value={host} onChange={(e) => setHost(e.target.value)} placeholder="smtp.example.com" />
        </div>
        <div className="field">
          <label>Port</label>
          <input className="input" type="number" value={port} onChange={(e) => setPort(Number(e.target.value))} />
        </div>
      </div>

      <div className="field-grid">
        <div className="field">
          <label>Username</label>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="field">
          <label>Password {settings?.password_set && <span className="badge badge-info">set</span>}</label>
          <input
            className="input"
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder={settings?.password_set ? 'Leave blank to keep' : ''}
          />
        </div>
      </div>

      <div className="row gap-16 mb-16">
        <label className="row gap-8">
          <input type="checkbox" checked={useTls} onChange={(e) => setUseTls(e.target.checked)} /> Use STARTTLS
        </label>
        <label className="row gap-8">
          <input type="checkbox" checked={useSsl} onChange={(e) => setUseSsl(e.target.checked)} /> Use SSL
        </label>
      </div>

      <div className="field-grid">
        <div className="field">
          <label>From address</label>
          <input className="input" value={fromAddress} onChange={(e) => setFromAddress(e.target.value)} placeholder="trackly@example.com" />
        </div>
        <div className="field">
          <label>From name</label>
          <input className="input" value={fromName} onChange={(e) => setFromName(e.target.value)} placeholder="Trackly" />
        </div>
      </div>

      <button className="btn btn-primary" onClick={save} disabled={busy}>
        {busy ? 'Saving…' : 'Save changes'}
      </button>

      <div className="section-card mt-24">
        <h3>Send test email</h3>
        <p className="muted text-sm mb-8">Verify the SMTP configuration by sending a test message.</p>
        <div className="row gap-8 wrap" style={{ alignItems: 'flex-end' }}>
          <div className="flex-1" style={{ minWidth: 200 }}>
            <input className="input" type="email" value={testTo} onChange={(e) => setTestTo(e.target.value)} placeholder="recipient@example.com" />
          </div>
          <button className="btn" onClick={sendTest} disabled={busy || !testTo.trim()}>
            Send test email
          </button>
        </div>
      </div>

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
