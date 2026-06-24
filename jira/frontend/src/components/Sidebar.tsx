import { useEffect, useState } from 'react';
import { NavLink, useLocation, useParams } from 'react-router-dom';
import { ProjectBrief } from '../types';
import { listProjects } from '../api/projects';

const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
function colorFor(key: string, fallback?: string | null): string {
  if (fallback) return fallback;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

export function Sidebar() {
  const [projects, setProjects] = useState<ProjectBrief[]>([]);
  const { projectKey } = useParams();
  const location = useLocation();

  useEffect(() => {
    listProjects().then(setProjects).catch(() => {});
  }, []);

  const active = projects.find((p) => p.key === projectKey || p.id === projectKey);

  return (
    <nav className="sidebar">
      <NavLink to="/projects" className={`nav-item ${location.pathname === '/projects' ? 'active' : ''}`}>
        <span style={{ width: 26, textAlign: 'center' }}>🗂</span> Projects
      </NavLink>
      <NavLink to="/search" className={`nav-item ${location.pathname.startsWith('/search') ? 'active' : ''}`}>
        <span style={{ width: 26, textAlign: 'center' }}>🔍</span> Search issues
      </NavLink>

      {active && (
        <>
          <div className="sidebar-section-title">{active.name}</div>
          <NavLink to={`/projects/${active.key}/board`} className="nav-item">
            <span style={{ width: 26, textAlign: 'center' }}>📋</span> Board
          </NavLink>
          {active.project_type === 'scrum' && (
            <NavLink to={`/projects/${active.key}/backlog`} className="nav-item">
              <span style={{ width: 26, textAlign: 'center' }}>📚</span> Backlog
            </NavLink>
          )}
          <NavLink to={`/projects/${active.key}/insights`} className="nav-item">
            <span style={{ width: 26, textAlign: 'center' }}>📊</span> Insights
          </NavLink>
          <NavLink to={`/projects/${active.key}/settings`} className="nav-item">
            <span style={{ width: 26, textAlign: 'center' }}>⚙️</span> Settings
          </NavLink>
        </>
      )}

      <div className="sidebar-section-title">All projects</div>
      {projects.map((p) => (
        <NavLink
          key={p.id}
          to={`/projects/${p.key}/board`}
          className={`nav-item ${active?.id === p.id ? 'active' : ''}`}
        >
          <span className="nav-project-key" style={{ background: colorFor(p.key, p.avatar_color) }}>
            {p.key.slice(0, 2)}
          </span>
          <span className="nowrap" style={{ overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {p.name}
          </span>
        </NavLink>
      ))}
    </nav>
  );
}
