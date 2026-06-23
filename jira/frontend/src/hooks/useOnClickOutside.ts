import { RefObject, useEffect } from 'react';

export function useOnClickOutside(ref: RefObject<HTMLElement>, handler: () => void, active = true) {
  useEffect(() => {
    if (!active) return;
    const listener = (e: MouseEvent) => {
      if (!ref.current || ref.current.contains(e.target as Node)) return;
      handler();
    };
    document.addEventListener('mousedown', listener);
    return () => document.removeEventListener('mousedown', listener);
  }, [ref, handler, active]);
}
