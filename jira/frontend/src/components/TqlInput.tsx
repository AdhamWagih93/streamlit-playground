import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { TqlField, TqlSchema, TqlValue } from '../types';
import { getTqlValues, validateTql } from '../api/search';

// ---------------------------------------------------------------------------
// Lightweight tokenizer — splits on whitespace, honours quotes, treats
// parens / commas / operator runs (= ! < > ~) as their own tokens.
// ---------------------------------------------------------------------------

type TokKind = 'word' | 'string' | 'op' | 'paren' | 'comma';
interface Tok {
  text: string;
  start: number;
  end: number;
  kind: TokKind;
}

const OP_CHARS = '=!<>~';
const DELIMS = '(),"\'';

function tokenize(s: string): Tok[] {
  const toks: Tok[] = [];
  let i = 0;
  while (i < s.length) {
    const c = s[i];
    if (/\s/.test(c)) {
      i++;
      continue;
    }
    if (c === '"' || c === "'") {
      const start = i;
      i++;
      while (i < s.length && s[i] !== c) i++;
      if (i < s.length) i++; // consume closing quote
      toks.push({ text: s.slice(start, i), start, end: i, kind: 'string' });
      continue;
    }
    if (c === '(' || c === ')') {
      toks.push({ text: c, start: i, end: i + 1, kind: 'paren' });
      i++;
      continue;
    }
    if (c === ',') {
      toks.push({ text: c, start: i, end: i + 1, kind: 'comma' });
      i++;
      continue;
    }
    if (OP_CHARS.includes(c)) {
      const start = i;
      while (i < s.length && OP_CHARS.includes(s[i])) i++;
      toks.push({ text: s.slice(start, i), start, end: i, kind: 'op' });
      continue;
    }
    const start = i;
    while (i < s.length && !/\s/.test(s[i]) && !DELIMS.includes(s[i]) && !OP_CHARS.includes(s[i])) i++;
    toks.push({ text: s.slice(start, i), start, end: i, kind: 'word' });
  }
  return toks;
}

// ---------------------------------------------------------------------------
// Context detection
// ---------------------------------------------------------------------------

type Ctx =
  | { kind: 'field'; partial: string; replaceStart: number }
  | { kind: 'operator'; field: TqlField; partial: string; replaceStart: number }
  | { kind: 'value'; field: TqlField; partial: string; q: string; replaceStart: number; inList: boolean }
  | { kind: 'inparen'; field: TqlField; partial: string; replaceStart: number }
  | { kind: 'connector'; partial: string; replaceStart: number }
  | { kind: 'orderfield'; partial: string; replaceStart: number }
  | { kind: 'orderdir'; partial: string; replaceStart: number }
  | { kind: 'none'; partial: string; replaceStart: number };

const CONNECTORS = ['AND', 'OR', 'ORDER BY'];

function stripQuotes(s: string): string {
  return s.replace(/^["']/, '').replace(/["']$/, '');
}

function detect(value: string, caret: number, schema: TqlSchema): Ctx {
  const before = value.slice(0, caret);
  const toks = tokenize(before);
  const fieldByName = (t: string): TqlField | undefined =>
    schema.fields.find((f) => f.name.toLowerCase() === t.toLowerCase());

  // Split off a partial token touching the caret.
  let partial = '';
  let replaceStart = caret;
  let completed = toks;
  const endsWithSpace = before.length > 0 && /\s/.test(before[before.length - 1]);
  if (!endsWithSpace && toks.length) {
    const last = toks[toks.length - 1];
    if (last.end === before.length && (last.kind === 'word' || last.kind === 'string' || last.kind === 'op')) {
      partial = last.text;
      replaceStart = last.start;
      completed = toks.slice(0, -1);
    }
  }

  const lower = (t: Tok) => t.text.toLowerCase();

  // --- ORDER BY mode (always trails the query) ---
  let orderIdx = -1;
  for (let i = 0; i < completed.length - 1; i++) {
    if (lower(completed[i]) === 'order' && lower(completed[i + 1]) === 'by') orderIdx = i + 1;
  }
  if (orderIdx >= 0) {
    const tail = completed.slice(orderIdx + 1);
    const last = tail[tail.length - 1];
    if (!last || last.kind === 'comma') return { kind: 'orderfield', partial, replaceStart };
    if (fieldByName(last.text)) return { kind: 'orderdir', partial, replaceStart };
    const lt = last.text.toLowerCase();
    if (lt === 'asc' || lt === 'desc') return { kind: 'none', partial, replaceStart };
    return { kind: 'orderfield', partial, replaceStart };
  }

  const last = completed[completed.length - 1];
  if (!last) return { kind: 'field', partial, replaceStart };

  const lt = last.text.toLowerCase();

  // After a connector / open grouping paren → expect a field.
  if (lt === 'and' || lt === 'or') return { kind: 'field', partial, replaceStart };

  // Operator keyword IN / NOT IN → expect an opening paren.
  const isInKeyword = lt === 'in';
  if (isInKeyword) {
    // field is the token before 'in' (skip a leading 'not')
    let idx = completed.length - 2;
    if (idx >= 0 && completed[idx].text.toLowerCase() === 'not') idx--;
    const f = idx >= 0 ? fieldByName(completed[idx].text) : undefined;
    if (f) return { kind: 'inparen', field: f, partial, replaceStart };
  }

  if (last.kind === 'paren' && last.text === '(') {
    const prev = completed[completed.length - 2];
    if (prev && prev.text.toLowerCase() === 'in') {
      let idx = completed.length - 3;
      if (idx >= 0 && completed[idx].text.toLowerCase() === 'not') idx--;
      const f = idx >= 0 ? fieldByName(completed[idx].text) : undefined;
      if (f) return { kind: 'value', field: f, partial, q: stripQuotes(partial), replaceStart, inList: true };
    }
    return { kind: 'field', partial, replaceStart };
  }

  if (last.kind === 'comma') {
    // inside an IN (...) list — find the field that owns the open list
    let inIdx = -1;
    for (let i = completed.length - 1; i >= 0; i--) {
      if (completed[i].text.toLowerCase() === 'in') {
        inIdx = i;
        break;
      }
    }
    if (inIdx >= 0) {
      let idx = inIdx - 1;
      if (idx >= 0 && completed[idx].text.toLowerCase() === 'not') idx--;
      const f = idx >= 0 ? fieldByName(completed[idx].text) : undefined;
      if (f) return { kind: 'value', field: f, partial, q: stripQuotes(partial), replaceStart, inList: true };
    }
    return { kind: 'none', partial, replaceStart };
  }

  // Closing paren of an IN list → expect a connector.
  if (last.kind === 'paren' && last.text === ')') return { kind: 'connector', partial, replaceStart };

  // A field name → expect an operator.
  const asField = fieldByName(last.text);
  if (asField && last.kind === 'word') return { kind: 'operator', field: asField, partial, replaceStart };

  // A symbol operator → expect a value (field is the token before it).
  if (last.kind === 'op') {
    const prev = completed[completed.length - 2];
    const f = prev ? fieldByName(prev.text) : undefined;
    if (f) return { kind: 'value', field: f, partial, q: stripQuotes(partial), replaceStart, inList: false };
  }

  // Anything else (a completed value: quoted string, number, value word) → connector.
  return { kind: 'connector', partial, replaceStart };
}

// ---------------------------------------------------------------------------

interface Suggestion {
  text: string;
  hint?: string;
  insert: string;
}

const DATE_HINTS: Suggestion[] = [
  { text: '0d', hint: 'today', insert: '0d ' },
  { text: '-7d', hint: '7 days ago', insert: '-7d ' },
  { text: '-1w', hint: '1 week ago', insert: '-1w ' },
  { text: '-1m', hint: '1 month ago', insert: '-1m ' },
  { text: 'YYYY-MM-DD', hint: 'an exact date', insert: '' },
];

const NUMBER_HINTS: Suggestion[] = ['1', '2', '3', '5', '8', '13'].map((n) => ({
  text: n,
  insert: `${n} `,
}));

function matches(text: string, partial: string): boolean {
  if (!partial) return true;
  return text.toLowerCase().includes(partial.toLowerCase());
}

interface Props {
  value: string;
  onChange: (v: string) => void;
  onSubmit?: () => void;
  schema: TqlSchema | null;
  placeholder?: string;
  autoFocus?: boolean;
}

export function TqlInput({ value, onChange, onSubmit, schema, placeholder, autoFocus }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const wrapRef = useRef<HTMLDivElement>(null);
  const pendingCaret = useRef<number | null>(null);
  const [caret, setCaret] = useState(value.length);
  const [highlight, setHighlight] = useState(0);
  const [closed, setClosed] = useState(false);
  const [focused, setFocused] = useState(false);
  const [valuesState, setValuesState] = useState<{ field: string; q: string; items: TqlValue[] }>({
    field: '',
    q: '',
    items: [],
  });
  const [validation, setValidation] = useState<{ valid: boolean; error?: string | null } | null>(null);

  const ctx = useMemo<Ctx | null>(() => {
    if (!schema) return null;
    try {
      return detect(value, caret, schema);
    } catch {
      return null;
    }
  }, [value, caret, schema]);

  // Fetch async values for value-context fields (debounced).
  useEffect(() => {
    if (!ctx || ctx.kind !== 'value' || !ctx.field.values) return;
    const field = ctx.field.name;
    const q = ctx.q;
    let alive = true;
    const t = setTimeout(() => {
      getTqlValues(field, q)
        .then((items) => alive && setValuesState({ field, q, items }))
        .catch(() => alive && setValuesState({ field, q, items: [] }));
    }, 150);
    return () => {
      alive = false;
      clearTimeout(t);
    };
  }, [ctx]);

  // Live validation (debounced) — never blocks typing.
  useEffect(() => {
    if (!value.trim()) {
      setValidation(null);
      return;
    }
    const t = setTimeout(() => {
      validateTql(value)
        .then(setValidation)
        .catch(() => setValidation(null));
    }, 350);
    return () => clearTimeout(t);
  }, [value]);

  const suggestions = useMemo<Suggestion[]>(() => {
    if (!ctx || !schema) return [];
    const p = ctx.partial;
    switch (ctx.kind) {
      case 'field':
      case 'orderfield':
        return schema.fields
          .filter((f) => matches(f.name, p))
          .map((f) => ({ text: f.name, hint: f.description, insert: `${f.name} ` }));
      case 'operator':
        return ctx.field.operators
          .filter((op) => matches(op, p))
          .map((op) => ({
            text: op,
            hint: OP_LABELS[op],
            insert: op === 'IN' || op === 'NOT IN' ? `${op} (` : `${op} `,
          }));
      case 'inparen':
        return matches('(', p) ? [{ text: '(', hint: 'open value list', insert: '(' }] : [];
      case 'connector':
        return CONNECTORS.filter((k) => matches(k, p)).map((k) => ({ text: k, insert: `${k} ` }));
      case 'orderdir':
        return ['ASC', 'DESC'].filter((d) => matches(d, p)).map((d) => ({ text: d, insert: `${d} ` }));
      case 'value': {
        const out: Suggestion[] = [];
        if (ctx.field.values) {
          // user-type fields: make sure currentUser()/empty are reachable
          if (ctx.field.type === 'user') {
            for (const extra of ['currentUser()', 'empty']) {
              if (matches(extra, p) && !valuesState.items.some((v) => v.value === extra)) {
                out.push({
                  text: extra,
                  hint: extra === 'currentUser()' ? 'the signed-in user' : 'no one assigned',
                  insert: `${extra} `,
                });
              }
            }
          }
          if (valuesState.field === ctx.field.name) {
            for (const v of valuesState.items) {
              out.push({ text: v.label || v.value, hint: v.hint || undefined, insert: `${v.value} ` });
            }
          }
          return out;
        }
        if (ctx.field.type === 'date') return DATE_HINTS.filter((d) => matches(d.text, p));
        if (ctx.field.type === 'number') return NUMBER_HINTS.filter((d) => matches(d.text, p));
        return [];
      }
      default:
        return [];
    }
  }, [ctx, schema, valuesState]);

  const open = focused && !closed && suggestions.length > 0;

  // Keep the highlighted row in range.
  useLayoutEffect(() => {
    setHighlight((h) => (h >= suggestions.length ? 0 : h));
  }, [suggestions.length]);

  // Restore caret after an accept rewrites the value.
  useLayoutEffect(() => {
    if (pendingCaret.current != null && inputRef.current) {
      const pos = pendingCaret.current;
      pendingCaret.current = null;
      inputRef.current.setSelectionRange(pos, pos);
      setCaret(pos);
    }
  }, [value]);

  function syncCaret() {
    const el = inputRef.current;
    if (el && el.selectionStart != null) setCaret(el.selectionStart);
  }

  function accept(s: Suggestion) {
    if (!ctx || !s.insert) {
      // Non-insertable hint (e.g. the YYYY-MM-DD placeholder) — ignore.
      return;
    }
    const start = ctx.replaceStart;
    const next = value.slice(0, start) + s.insert + value.slice(caret);
    pendingCaret.current = start + s.insert.length;
    setClosed(false);
    setHighlight(0);
    onChange(next);
    inputRef.current?.focus();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (open) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setHighlight((h) => (h + 1) % suggestions.length);
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setHighlight((h) => (h - 1 + suggestions.length) % suggestions.length);
        return;
      }
      if (e.key === 'Enter' || e.key === 'Tab') {
        const s = suggestions[highlight];
        if (s && s.insert) {
          e.preventDefault();
          accept(s);
          return;
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setClosed(true);
        return;
      }
    }
    if (e.key === 'Enter' && !open) {
      e.preventDefault();
      onSubmit?.();
    }
  }

  // Degrade to a plain input if the schema failed to load.
  if (!schema) {
    return (
      <input
        className="input tql-plain"
        style={{ fontFamily: 'monospace' }}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        onKeyDown={(e) => {
          if (e.key === 'Enter') {
            e.preventDefault();
            onSubmit?.();
          }
        }}
      />
    );
  }

  const validClass = validation ? (validation.valid ? 'valid' : 'invalid') : '';

  return (
    <div className="tql-input-wrap" ref={wrapRef}>
      <div className={`tql-input-box ${validClass}`}>
        <input
          ref={inputRef}
          className="tql-input"
          // eslint-disable-next-line jsx-a11y/no-autofocus
          autoFocus={autoFocus}
          value={value}
          placeholder={placeholder}
          spellCheck={false}
          autoComplete="off"
          role="combobox"
          aria-expanded={open}
          aria-autocomplete="list"
          aria-controls="tql-suggest-list"
          onChange={(e) => {
            onChange(e.target.value);
            setClosed(false);
            setHighlight(0);
            // caret moves synchronously with the new value
            requestAnimationFrame(syncCaret);
          }}
          onKeyUp={syncCaret}
          onClick={syncCaret}
          onKeyDown={onKeyDown}
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
      </div>

      {open && (
        <ul className="tql-suggest menu" id="tql-suggest-list" role="listbox">
          {suggestions.map((s, i) => (
            <li
              key={`${s.text}-${i}`}
              role="option"
              aria-selected={i === highlight}
              className={`tql-suggest-item ${i === highlight ? 'active' : ''}`}
              // onMouseDown (not click) so it fires before the input blurs
              onMouseDown={(e) => {
                e.preventDefault();
                accept(s);
              }}
              onMouseEnter={() => setHighlight(i)}
            >
              <span className="tql-suggest-text">{s.text}</span>
              {s.hint && <span className="tql-suggest-hint">{s.hint}</span>}
            </li>
          ))}
        </ul>
      )}

      {validation && (
        <div className={`tql-validation ${validation.valid ? 'ok' : 'err'}`}>
          {validation.valid ? '✓ valid query' : `✗ ${validation.error || 'invalid query'}`}
        </div>
      )}
    </div>
  );
}

const OP_LABELS: Record<string, string> = {
  '=': 'equals',
  '!=': 'not equals',
  '~': 'contains',
  '!~': 'does not contain',
  '>': 'greater than',
  '<': 'less than',
  '>=': 'on or after / at least',
  '<=': 'on or before / at most',
  IN: 'one of',
  'NOT IN': 'none of',
};
