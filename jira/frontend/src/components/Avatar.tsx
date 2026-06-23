import { User } from '../types';

interface Props {
  user?: User | null;
  name?: string;
  size?: number;
  title?: string;
}

const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];

function colorFor(seed: string): string {
  let h = 0;
  for (let i = 0; i < seed.length; i++) h = (h * 31 + seed.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}

export function Avatar({ user, name, size = 28, title }: Props) {
  const label = user?.display_name || name || 'Unassigned';
  const seed = user?.id || user?.email || label;
  const style: React.CSSProperties = {
    width: size,
    height: size,
    fontSize: Math.max(10, size * 0.42),
    background: user?.avatar_url ? undefined : colorFor(seed),
    backgroundImage: user?.avatar_url ? `url(${user.avatar_url})` : undefined,
  };
  return (
    <span className="avatar" style={style} title={title || label}>
      {!user?.avatar_url && initials(label)}
    </span>
  );
}
