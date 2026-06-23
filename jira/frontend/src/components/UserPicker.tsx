import { useEffect, useRef, useState } from 'react';
import { User } from '../types';
import { searchUsers } from '../api/users';
import { Avatar } from './Avatar';
import { useOnClickOutside } from '../hooks/useOnClickOutside';

interface Props {
  value: User | null;
  onChange: (user: User | null) => void;
  placeholder?: string;
  allowUnassigned?: boolean;
}

export function UserPicker({ value, onChange, placeholder = 'Search people…', allowUnassigned = true }: Props) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<User[]>([]);
  const [loading, setLoading] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => setOpen(false), open);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    setLoading(true);
    const t = setTimeout(() => {
      searchUsers(query)
        .then((r) => alive && setResults(r))
        .finally(() => alive && setLoading(false));
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [query, open]);

  function pick(u: User | null) {
    onChange(u);
    setOpen(false);
    setQuery('');
  }

  return (
    <div className="autocomplete" ref={ref}>
      <button type="button" className="btn btn-block" style={{ justifyContent: 'flex-start' }} onClick={() => setOpen((o) => !o)}>
        {value ? (
          <span className="row gap-8">
            <Avatar user={value} size={20} />
            {value.display_name}
          </span>
        ) : (
          <span className="muted">Unassigned</span>
        )}
      </button>
      {open && (
        <div className="autocomplete-list">
          <div style={{ padding: 8 }}>
            <input
              className="input"
              autoFocus
              value={query}
              placeholder={placeholder}
              onChange={(e) => setQuery(e.target.value)}
            />
          </div>
          {allowUnassigned && (
            <div className="autocomplete-item" onClick={() => pick(null)}>
              <span className="avatar" style={{ width: 20, height: 20, background: '#cbd5e1', fontSize: 11 }}>?</span>
              <span className="muted">Unassigned</span>
            </div>
          )}
          {loading && <div className="autocomplete-item muted">Searching…</div>}
          {!loading && results.length === 0 && <div className="autocomplete-item muted">No people found</div>}
          {results.map((u) => (
            <div key={u.id} className="autocomplete-item" onClick={() => pick(u)}>
              <Avatar user={u} size={22} />
              <div className="col">
                <span>{u.display_name}</span>
                <span className="text-xs muted">{u.email}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
