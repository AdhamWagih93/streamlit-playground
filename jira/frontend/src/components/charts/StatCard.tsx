import { ReactNode } from 'react';

interface Props {
  label: string;
  value: ReactNode;
  hint?: string;
  accent?: 'indigo' | 'green' | 'amber' | 'blue' | 'slate' | 'red';
}

export function StatCard({ label, value, hint, accent = 'indigo' }: Props) {
  return (
    <div className={`stat-card stat-${accent}`}>
      <div className="stat-label">{label}</div>
      <div className="stat-value">{value}</div>
      {hint && <div className="stat-hint">{hint}</div>}
    </div>
  );
}
