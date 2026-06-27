import { useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useAuth } from '../store/auth';
import { useUI } from '../store/ui';
import { Avatar } from './Avatar';
import { NotificationsBell } from './NotificationsBell';
import { useOnClickOutside } from '../hooks/useOnClickOutside';

export function TopBar() {
  const user = useAuth((s) => s.user);
  const logout = useAuth((s) => s.logout);
  const openCreateIssue = useUI((s) => s.openCreateIssue);
  const navigate = useNavigate();
  const [search, setSearch] = useState('');
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);
  useOnClickOutside(menuRef, () => setMenuOpen(false), menuOpen);

  function onSearch(e: React.FormEvent) {
    e.preventDefault();
    const q = search.trim();
    navigate(q ? `/search?tql=${encodeURIComponent(q)}` : '/search');
  }

  function doLogout() {
    logout();
    navigate('/login');
  }

  return (
    <header className="topbar">
      <div className="brand pointer" onClick={() => navigate('/projects')}>
        <span className="brand-mark">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3">
            <path d="M5 13l4 4L19 7" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
        Trackly
      </div>

      <form className="topbar-search" onSubmit={onSearch}>
        <span className="search-icon">
          <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="11" cy="11" r="7" />
            <path d="M21 21l-4.3-4.3" strokeLinecap="round" />
          </svg>
        </span>
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder='Search TQL — e.g. assignee = currentUser() AND statusCategory != done'
        />
      </form>

      <div className="topbar-spacer" />

      <div className="topbar-actions">
        <button className="btn btn-primary" onClick={() => openCreateIssue()}>
          <span style={{ fontSize: 16, lineHeight: 1 }}>+</span> Create
        </button>
        <NotificationsBell />
        <div className="bell-wrap" ref={menuRef}>
          <button className="bell-btn" onClick={() => setMenuOpen((o) => !o)} style={{ width: 'auto', padding: 4 }}>
            <Avatar user={user} size={30} />
          </button>
          {menuOpen && (
            <div className="menu" style={{ right: 0, top: 44 }}>
              <div style={{ padding: '6px 10px' }}>
                <div style={{ fontWeight: 600 }}>{user?.display_name}</div>
                <div className="text-xs muted">{user?.email}</div>
              </div>
              <div className="menu-divider" />
              <button className="menu-item" onClick={() => { setMenuOpen(false); navigate('/profile'); }}>
                Profile
              </button>
              <button className="menu-item" onClick={() => { setMenuOpen(false); navigate('/search'); }}>
                Saved filters
              </button>
              {user?.is_admin && (
                <button className="menu-item" onClick={() => { setMenuOpen(false); navigate('/admin/insights'); }}>
                  Insights
                </button>
              )}
              {user?.is_admin && (
                <button className="menu-item" onClick={() => { setMenuOpen(false); navigate('/admin'); }}>
                  Administration
                </button>
              )}
              <div className="menu-divider" />
              <button className="menu-item" onClick={doLogout}>
                Sign out
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
