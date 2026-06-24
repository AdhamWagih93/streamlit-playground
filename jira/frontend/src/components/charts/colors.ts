import { StatusCategory } from '../../types';

// Status-category colors per spec: todo=slate, in_progress=blue/amber, done=green.
export function categoryColor(category?: StatusCategory | null): string {
  switch (category) {
    case 'done':
      return '#22c55e';
    case 'in_progress':
      return '#2563eb';
    case 'todo':
    default:
      return '#94a3b8';
  }
}

// Resolve a slice/bar color: explicit color > category color > palette fallback.
const PALETTE = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];

export function resolveColor(
  opts: { color?: string | null; category?: StatusCategory | null },
  index = 0
): string {
  if (opts.color) return opts.color;
  if (opts.category) return categoryColor(opts.category);
  return PALETTE[index % PALETTE.length];
}
