import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { User } from '../types';
import { searchUsers } from '../api/users';
import { useAuth } from '../store/auth';
import { apiErrorMessage } from '../api/client';
import { Modal } from './Modal';
import { Avatar } from './Avatar';

interface Props {
  open: boolean;
  onClose: () => void;
}

export function ImpersonateModal({ open, onClose }: Props) {
  const me = useAuth((s) => s.user);
  const impersonate = useAuth((s) => s.impersonate);
  const navigate = useNavigate();

  const [query, setQuery] = useState('');
  const [results, setResults] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Reset transient state each time the modal opens.
  useEffect(() => {
    if (open) {
      setQuery('');
      setResults([]);
      setError(null);
      setBusyId(null);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    const t = setTimeout(() => {
      searchUsers(query)
        .then((r) => alive && setResults(r))
        .catch(() => alive && setResults([]))
        .finally(() => alive && setLoading(false));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [query, open]);

  async function pick(u: User) {
    setError(null);
    setBusyId(u.id);
    try {
      await impersonate(Number(u.id));
      onClose();
      navigate('/');
    } catch (err) {
      setError(apiErrorMessage(err, 'Could not view as this user'));
      setBusyId(null);
    }
  }

  // Hide the current admin from the list (backend would 400 on self anyway).
  const visible = results.filter((u) => u.id !== me?.id);

  return (
    <Modal open={open} title="View as user" onClose={onClose}>
      <p className="text-xs muted" style={{ marginTop: 0 }}>
        Browse Trackly as another user. You’ll see exactly their view and permissions, and can
        return to your own account at any time from the banner.
      </p>
      <input
        className="input"
        autoFocus
        value={query}
        placeholder="Search people by name, username or email…"
        onChange={(e) => setQuery(e.target.value)}
      />
      {error && (
        <div className="text-xs" style={{ color: 'var(--red-500)', marginTop: 8 }}>
          {error}
        </div>
      )}
      <div className="impersonate-list">
        {loading && <div className="autocomplete-item muted">Searching…</div>}
        {!loading && visible.length === 0 && (
          <div className="autocomplete-item muted">No people found</div>
        )}
        {visible.map((u) => (
          <button
            key={u.id}
            type="button"
            className="impersonate-item"
            disabled={busyId !== null}
            onClick={() => pick(u)}
          >
            <Avatar user={u} size={28} />
            <div className="col" style={{ flex: 1, minWidth: 0 }}>
              <span className="impersonate-name">
                {u.display_name}
                {u.is_admin && <span className="impersonate-badge">admin</span>}
              </span>
              <span className="text-xs muted">{u.email}</span>
            </div>
            <span className="text-xs impersonate-cta">
              {busyId === u.id ? 'Switching…' : 'View as'}
            </span>
          </button>
        ))}
      </div>
    </Modal>
  );
}
