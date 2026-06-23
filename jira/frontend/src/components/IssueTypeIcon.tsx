import { IssueRefType, IssueType } from '../types';

interface Props {
  type?: IssueRefType | IssueType | null;
  size?: number;
}

const DEFAULTS: Record<string, { color: string; glyph: string }> = {
  story: { color: '#22c55e', glyph: 'S' },
  bug: { color: '#ef4444', glyph: 'B' },
  task: { color: '#6366f1', glyph: 'T' },
  epic: { color: '#8b5cf6', glyph: 'E' },
  'sub-task': { color: '#0ea5e9', glyph: 's' },
  subtask: { color: '#0ea5e9', glyph: 's' },
};

export function IssueTypeIcon({ type, size = 16 }: Props) {
  if (!type) return null;
  const key = type.name?.toLowerCase() ?? '';
  const fallback = DEFAULTS[key] || { color: '#64748b', glyph: type.name?.[0]?.toUpperCase() || '?' };
  const color = type.color || fallback.color;
  const glyph = type.icon || fallback.glyph;
  return (
    <span
      className="type-icon"
      style={{ background: color, width: size, height: size, fontSize: size * 0.62 }}
      title={type.name}
    >
      {glyph.slice(0, 1)}
    </span>
  );
}
