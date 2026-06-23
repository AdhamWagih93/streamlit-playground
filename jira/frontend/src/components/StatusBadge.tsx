import { IssueRefStatus, Status, StatusCategory } from '../types';

interface Props {
  status?: IssueRefStatus | Status | null;
  category?: StatusCategory;
  name?: string;
}

export function StatusBadge({ status, category, name }: Props) {
  const cat = status?.category || category || 'todo';
  const label = status?.name || name || '—';
  return <span className={`status-badge status-${cat}`}>{label}</span>;
}
