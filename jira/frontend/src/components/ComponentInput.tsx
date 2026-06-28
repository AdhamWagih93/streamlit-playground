import { useEffect, useMemo, useRef, useState } from 'react';
import { Component } from '../types';
import { listComponents, createComponent } from '../api/projects';
import { apiErrorMessage } from '../api/client';
import { useOnClickOutside } from '../hooks/useOnClickOutside';

interface Props {
  projectId: string;
  value: string[]; // selected component ids
  onChange: (ids: string[]) => void;
}

// Multi-select for a project's components. Existing components are suggested as
// you type; an unmatched name can be created inline (project admins only — the
// API rejects others, and we surface that message).
export function ComponentInput({ projectId, value, onChange }: Props) {
  const [components, setComponents] = useState<Component[]>([]);
  const [text, setText] = useState('');
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => setOpen(false), open);

  useEffect(() => {
    setError('');
    if (!projectId) {
      setComponents([]);
      return;
    }
    let alive = true;
    listComponents(projectId)
      .then((c) => alive && setComponents(c))
      .catch(() => alive && setComponents([]));
    return () => {
      alive = false;
    };
  }, [projectId]);

  const byId = useMemo(() => new Map(components.map((c) => [c.id, c])), [components]);

  const query = text.trim().toLowerCase();
  const suggestions = components.filter(
    (c) => !value.includes(c.id) && c.name.toLowerCase().includes(query)
  );
  const exact = components.find((c) => c.name.toLowerCase() === query);

  function select(id: string) {
    if (!value.includes(id)) onChange([...value, id]);
    setText('');
    setError('');
  }

  function remove(id: string) {
    onChange(value.filter((v) => v !== id));
  }

  async function createNew(name: string) {
    const trimmed = name.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError('');
    try {
      const created = await createComponent(projectId, { name: trimmed });
      setComponents((cur) => [...cur, created]);
      onChange([...value, created.id]);
      setText('');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not add component'));
    } finally {
      setBusy(false);
    }
  }

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter') {
      e.preventDefault();
      if (exact) select(exact.id);
      else if (query) createNew(text);
    } else if (e.key === 'Backspace' && !text && value.length) {
      remove(value[value.length - 1]);
    }
  }

  if (!projectId) {
    return <div className="hint">Select a project to choose components.</div>;
  }

  return (
    <div className="autocomplete" ref={ref}>
      <div className="token-input" onClick={() => setOpen(true)}>
        {value.map((id) => (
          <span key={id} className="token">
            {byId.get(id)?.name || 'Component'}
            <button type="button" onClick={() => remove(id)}>
              ×
            </button>
          </span>
        ))}
        <input
          value={text}
          placeholder={value.length ? '' : 'Add or create component…'}
          onFocus={() => setOpen(true)}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
        />
      </div>
      {error && <div className="field-error">{error}</div>}
      {open && (suggestions.length > 0 || (query && !exact)) && (
        <div className="autocomplete-list">
          {suggestions.slice(0, 8).map((c) => (
            <div key={c.id} className="autocomplete-item" onClick={() => select(c.id)}>
              {c.name}
            </div>
          ))}
          {query && !exact && (
            <div className="autocomplete-item create" onClick={() => createNew(text)}>
              {busy ? 'Adding…' : <>+ Create “{text.trim()}”</>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
