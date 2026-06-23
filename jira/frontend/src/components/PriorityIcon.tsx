import { IssueRefPriority, Priority } from '../types';

interface Props {
  priority?: IssueRefPriority | Priority | null;
}

const ARROWS: Record<string, { glyph: string; color: string }> = {
  highest: { glyph: '⏫', color: '#dc2626' },
  high: { glyph: '🔺', color: '#ef4444' },
  medium: { glyph: '🟧', color: '#f59e0b' },
  low: { glyph: '🔻', color: '#3b82f6' },
  lowest: { glyph: '⏬', color: '#60a5fa' },
};

export function PriorityIcon({ priority }: Props) {
  if (!priority) return null;
  const key = priority.name?.toLowerCase() ?? '';
  const fallback = ARROWS[key] || { glyph: '◆', color: priority.color || '#64748b' };
  return (
    <span className="priority-icon" title={priority.name} style={{ color: priority.color || fallback.color }}>
      {priority.icon || fallback.glyph}
    </span>
  );
}
