import { useState } from 'react';
import { useAuth } from '../store/auth';
import { updateMe } from '../api/auth';
import { Avatar } from '../components/Avatar';
import { apiErrorMessage } from '../api/client';

export function ProfilePage() {
  const user = useAuth((s) => s.user);
  const setUser = useAuth((s) => s.setUser);
  const [displayName, setDisplayName] = useState(user?.display_name || '');
  const [timezone, setTimezone] = useState(user?.timezone || '');
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState('');
  const [error, setError] = useState('');

  async function save() {
    setBusy(true);
    setMsg('');
    setError('');
    try {
      const updated = await updateMe({ display_name: displayName, timezone });
      setUser(updated);
      setMsg('Profile updated');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not update profile'));
    } finally {
      setBusy(false);
    }
  }

  if (!user) return null;

  return (
    <div className="page" style={{ maxWidth: 560 }}>
      <div className="page-header">
        <h1 className="page-title">Profile</h1>
      </div>
      {msg && <div className="alert alert-success">{msg}</div>}
      {error && <div className="alert alert-error">{error}</div>}

      <div className="row gap-16 mb-16">
        <Avatar user={user} size={56} />
        <div>
          <div style={{ fontWeight: 600, fontSize: 16 }}>{user.display_name}</div>
          <div className="muted text-sm">{user.email}</div>
          {user.is_admin && <span className="ptype">Admin</span>}
        </div>
      </div>

      <div className="field">
        <label>Display name</label>
        <input className="input" value={displayName} onChange={(e) => setDisplayName(e.target.value)} />
      </div>
      <div className="field">
        <label>Username</label>
        <input className="input" value={user.username} disabled />
      </div>
      <div className="field">
        <label>Timezone</label>
        <input className="input" value={timezone} onChange={(e) => setTimezone(e.target.value)} placeholder="e.g. America/New_York" />
      </div>
      <button className="btn btn-primary" onClick={save} disabled={busy}>
        {busy ? 'Saving…' : 'Save'}
      </button>
    </div>
  );
}
