import { useRef, useState } from 'react';
import { useOnClickOutside } from '../hooks/useOnClickOutside';
import { Spinner } from './Spinner';

export interface ExportOption {
  label: string;
  format: string;
}

interface ExportMenuProps {
  options: ExportOption[];
  onSelect: (format: string) => void;
  disabled?: boolean;
  busy?: boolean;
}

// Reusable "Export ▾" button that opens a small menu of format options.
// Reuses the shared .menu/.menu-item dropdown styling and the click-outside hook.
export function ExportMenu({ options, onSelect, disabled, busy }: ExportMenuProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => setOpen(false), open);

  function choose(format: string) {
    setOpen(false);
    onSelect(format);
  }

  return (
    <div className="export-menu-wrap" ref={ref}>
      <button
        type="button"
        className="btn"
        onClick={() => setOpen((v) => !v)}
        disabled={disabled || busy}
        aria-haspopup="menu"
        aria-expanded={open}
      >
        {busy ? <Spinner /> : 'Export'} <span className="text-xs">▾</span>
      </button>
      {open && (
        <div className="menu export-menu" role="menu">
          {options.map((opt) => (
            <button
              key={opt.format}
              type="button"
              className="menu-item"
              role="menuitem"
              onClick={() => choose(opt.format)}
            >
              {opt.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
