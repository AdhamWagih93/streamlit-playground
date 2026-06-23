import { useEffect, useRef, useState } from 'react';
import { getLabels } from '../api/meta';
import { useOnClickOutside } from '../hooks/useOnClickOutside';

interface Props {
  value: string[];
  onChange: (labels: string[]) => void;
}

export function LabelInput({ value, onChange }: Props) {
  const [text, setText] = useState('');
  const [suggestions, setSuggestions] = useState<string[]>([]);
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => setOpen(false), open);

  useEffect(() => {
    let alive = true;
    const t = setTimeout(() => {
      getLabels(text)
        .then((r) => alive && setSuggestions(r.map((l) => l.name).filter((n) => !value.includes(n))))
        .catch(() => {});
    }, 200);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [text, value]);

  function add(label: string) {
    const v = label.trim();
    if (v && !value.includes(v)) onChange([...value, v]);
    setText('');
  }

  function remove(label: string) {
    onChange(value.filter((l) => l !== label));
  }

  return (
    <div className="autocomplete" ref={ref}>
      <div className="token-input" onClick={() => setOpen(true)}>
        {value.map((l) => (
          <span key={l} className="token">
            {l}
            <button type="button" onClick={() => remove(l)}>
              ×
            </button>
          </span>
        ))}
        <input
          value={text}
          placeholder="Add label…"
          onFocus={() => setOpen(true)}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              add(text);
            } else if (e.key === 'Backspace' && !text && value.length) {
              remove(value[value.length - 1]);
            }
          }}
        />
      </div>
      {open && suggestions.length > 0 && (
        <div className="autocomplete-list">
          {suggestions.slice(0, 8).map((s) => (
            <div key={s} className="autocomplete-item" onClick={() => add(s)}>
              {s}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
