import { useEffect } from 'react';

export interface ToastMsg {
  ok: boolean;
  text: string;
}

interface Props {
  toast: ToastMsg | null;
  onClose: () => void;
}

export function Toast({ toast, onClose }: Props) {
  useEffect(() => {
    if (!toast) return;
    const t = setTimeout(onClose, 4000);
    return () => clearTimeout(t);
  }, [toast, onClose]);

  if (!toast) return null;
  return (
    <div className={`toast ${toast.ok ? 'toast-ok' : 'toast-err'}`} onClick={onClose}>
      {toast.text}
    </div>
  );
}
