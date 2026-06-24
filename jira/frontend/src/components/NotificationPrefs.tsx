import { useEffect, useState } from 'react';
import { NotifPreferences, NotifChannel } from '../types';
import { getNotificationPreferences, updateNotificationPreferences } from '../api/notifyPrefs';
import { Spinner } from './Spinner';
import { apiErrorMessage } from '../api/client';

export function NotificationPrefs() {
  const [prefs, setPrefs] = useState<NotifPreferences | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [savingKey, setSavingKey] = useState('');

  useEffect(() => {
    getNotificationPreferences()
      .then(setPrefs)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  async function toggle(event: string, channel: NotifChannel, enabled: boolean) {
    setSavingKey(`${event}:${channel}`);
    setError('');
    try {
      const updated = await updateNotificationPreferences([{ event, channel, enabled }]);
      setPrefs(updated);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not update preference'));
    } finally {
      setSavingKey('');
    }
  }

  if (loading) return <div className="mt-16"><Spinner /></div>;
  if (!prefs) return null;

  return (
    <div className="mt-24">
      <h3 style={{ fontSize: 16, marginBottom: 4 }}>Notifications</h3>
      <p className="muted text-sm mb-8">Choose how you want to be notified about each event.</p>
      {error && <div className="alert alert-error">{error}</div>}
      {!prefs.email_available && (
        <div className="alert alert-info">Email notifications are disabled by the administrator.</div>
      )}

      <table className="data-table">
        <thead>
          <tr>
            <th>Event</th>
            <th className="checkbox-cell" style={{ width: 90 }}>In-app</th>
            <th className="checkbox-cell" style={{ width: 90 }}>Email</th>
          </tr>
        </thead>
        <tbody>
          {prefs.rows.map((row) => (
            <tr key={row.event}>
              <td>{row.label}</td>
              <td className="checkbox-cell">
                <input
                  type="checkbox"
                  checked={row.in_app}
                  disabled={savingKey === `${row.event}:in_app`}
                  onChange={(e) => toggle(row.event, 'in_app', e.target.checked)}
                />
              </td>
              <td className="checkbox-cell">
                <input
                  type="checkbox"
                  checked={row.email}
                  disabled={!prefs.email_available || savingKey === `${row.event}:email`}
                  onChange={(e) => toggle(row.event, 'email', e.target.checked)}
                />
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
