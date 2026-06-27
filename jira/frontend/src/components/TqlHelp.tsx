import { useRef, useState } from 'react';
import { TqlSchema } from '../types';
import { useOnClickOutside } from '../hooks/useOnClickOutside';

interface Props {
  schema: TqlSchema | null;
  // Set the query (and optionally run it) when an example is chosen.
  onPickExample: (query: string, run: boolean) => void;
}

// "?" affordance next to the TQL input. Opens a panel with clickable example
// queries and a compact field reference (name · description · operators).
export function TqlHelp({ schema, onPickExample }: Props) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useOnClickOutside(ref, () => setOpen(false), open);

  return (
    <div className="tql-help-wrap" ref={ref}>
      <button
        type="button"
        className="btn"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-expanded={open}
        title="TQL examples & field reference"
      >
        <span aria-hidden>?</span> Examples
      </button>

      {open && (
        <div className="tql-help-panel" role="dialog" aria-label="TQL help">
          <div className="tql-help-head">
            <strong>TQL — Trackly Query Language</strong>
            <button type="button" className="modal-close" onClick={() => setOpen(false)} aria-label="Close">
              ×
            </button>
          </div>

          <div className="tql-help-body">
            <div className="tql-help-section">
              <h5>Examples</h5>
              <div className="tql-example-list">
                {(schema?.examples ?? []).map((ex) => (
                  <div key={ex.query} className="tql-example-row">
                    <button
                      type="button"
                      className="tql-example-main"
                      onClick={() => {
                        onPickExample(ex.query, false);
                        setOpen(false);
                      }}
                    >
                      <span className="tql-example-label">{ex.label}</span>
                      <code className="tql-example-query">{ex.query}</code>
                    </button>
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      title="Run this query"
                      onClick={() => {
                        onPickExample(ex.query, true);
                        setOpen(false);
                      }}
                    >
                      Run ▸
                    </button>
                  </div>
                ))}
              </div>
            </div>

            <div className="tql-help-section">
              <h5>Fields</h5>
              <table className="tql-field-table">
                <tbody>
                  {(schema?.fields ?? []).map((f) => (
                    <tr key={f.name}>
                      <td>
                        <code>{f.name}</code>
                      </td>
                      <td className="muted">{f.description}</td>
                      <td className="tql-field-ops">{f.operators.join(' ')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="tql-help-section">
              <h5>Handy tokens</h5>
              <ul className="tql-help-tokens">
                <li>
                  <code>currentUser()</code> — the signed-in user, e.g.{' '}
                  <code>assignee = currentUser()</code>
                </li>
                <li>
                  <code>empty</code> — no value, e.g. <code>assignee = empty</code> (unassigned)
                </li>
                <li>
                  Relative dates: <code>0d</code> (today), <code>-7d</code>, <code>-1w</code>, <code>-1m</code>
                </li>
                <li>
                  Combine with <code>AND</code> / <code>OR</code> and sort with{' '}
                  <code>ORDER BY updated DESC</code>
                </li>
              </ul>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
