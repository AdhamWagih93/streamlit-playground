import { ReactNode } from 'react';

interface Props {
  icon?: string;
  title: string;
  message?: string;
  action?: ReactNode;
}

export function EmptyState({ icon = '📭', title, message, action }: Props) {
  return (
    <div className="empty-state">
      <div className="empty-icon">{icon}</div>
      <h3>{title}</h3>
      {message && <p>{message}</p>}
      {action && <div className="mt-16">{action}</div>}
    </div>
  );
}
