import { ReactNode, useEffect } from 'react';

interface Props {
  open: boolean;
  title?: ReactNode;
  onClose: () => void;
  children: ReactNode;
  footer?: ReactNode;
  size?: 'sm' | 'lg' | 'wide';
}

export function Modal({ open, title, onClose, children, footer, size }: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;
  const cls = size === 'lg' ? 'modal modal-lg' : size === 'wide' ? 'modal modal-wide' : 'modal';

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className={cls} onMouseDown={(e) => e.stopPropagation()}>
        {title !== undefined && (
          <div className="modal-header">
            <h3 className="modal-title">{title}</h3>
            <button className="modal-close" onClick={onClose} aria-label="Close">
              ×
            </button>
          </div>
        )}
        <div className="modal-body">{children}</div>
        {footer && <div className="modal-footer">{footer}</div>}
      </div>
    </div>
  );
}
