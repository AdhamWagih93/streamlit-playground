import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Notification } from '../types';
import { listNotifications, unreadCount, markRead, markAllRead } from '../api/notifications';
import { useOnClickOutside } from '../hooks/useOnClickOutside';
import { timeAgo } from '../lib/format';

function isUnread(n: Notification): boolean {
  return n.is_read === false || n.read === false;
}

export function NotificationsBell() {
  const [count, setCount] = useState(0);
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<Notification[]>([]);
  const ref = useRef<HTMLDivElement>(null);
  const navigate = useNavigate();
  useOnClickOutside(ref, () => setOpen(false), open);

  async function refreshCount() {
    try {
      setCount(await unreadCount());
    } catch {
      /* ignore */
    }
  }

  useEffect(() => {
    refreshCount();
    const t = setInterval(refreshCount, 30000);
    return () => clearInterval(t);
  }, []);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next) {
      try {
        setItems(await listNotifications(false));
      } catch {
        /* ignore */
      }
    }
  }

  async function onItem(n: Notification) {
    if (isUnread(n)) {
      try {
        await markRead(n.id);
        setItems((cur) => cur.map((x) => (x.id === n.id ? { ...x, is_read: true, read: true } : x)));
        refreshCount();
      } catch {
        /* ignore */
      }
    }
    if (n.issue_key) {
      setOpen(false);
      navigate(`/browse/${n.issue_key}`);
    }
  }

  async function readAll() {
    try {
      await markAllRead();
      setItems((cur) => cur.map((x) => ({ ...x, is_read: true, read: true })));
      setCount(0);
    } catch {
      /* ignore */
    }
  }

  return (
    <div className="bell-wrap" ref={ref}>
      <button className="bell-btn" onClick={toggle} aria-label="Notifications" title="Notifications">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
          <path d="M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9" strokeLinecap="round" strokeLinejoin="round" />
          <path d="M13.7 21a2 2 0 0 1-3.4 0" strokeLinecap="round" strokeLinejoin="round" />
        </svg>
        {count > 0 && <span className="bell-dot">{count > 99 ? '99+' : count}</span>}
      </button>
      {open && (
        <div className="notif-panel">
          <div className="notif-panel-head">
            <span>Notifications</span>
            <button className="btn btn-ghost btn-sm" onClick={readAll}>
              Mark all read
            </button>
          </div>
          {items.length === 0 && <div className="notif-item muted">You're all caught up 🎉</div>}
          {items.map((n) => (
            <div key={n.id} className={`notif-item ${isUnread(n) ? 'unread' : ''}`} onClick={() => onItem(n)}>
              {isUnread(n) && <span className="dot" />}
              <div className="flex-1">
                <div className="text-sm">
                  {n.title && <strong>{n.title} </strong>}
                  {n.body || n.message}
                </div>
                <div className="notif-time">
                  {n.issue_key ? `${n.issue_key} · ` : ''}
                  {timeAgo(n.created_at)}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
