import { useEffect, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { ProjectBrief, User, OverviewStats } from '../types';
import { listProjects, createProject } from '../api/projects';
import { getMyOverview } from '../api/analytics';
import { useAuth } from '../store/auth';
import { Modal } from '../components/Modal';
import { UserPicker } from '../components/UserPicker';
import { SpinnerCenter } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { StatCard } from '../components/charts/StatCard';
import { apiErrorMessage } from '../api/client';

const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899', '#14b8a6'];
function colorFor(key: string, fallback?: string | null): string {
  if (fallback) return fallback;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

export function ProjectsPage() {
  const user = useAuth((s) => s.user);
  const [projects, setProjects] = useState<ProjectBrief[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [my, setMy] = useState<OverviewStats | null>(null);

  function load() {
    setLoading(true);
    listProjects()
      .then(setProjects)
      .finally(() => setLoading(false));
  }

  useEffect(load, []);
  useEffect(() => {
    getMyOverview().then(setMy).catch(() => setMy(null));
  }, []);

  const topProjects = my
    ? [...my.projects].sort((a, b) => b.total_issues - a.total_issues).slice(0, 4)
    : [];

  return (
    <div className="page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Projects</h1>
          <div className="page-subtitle">Browse and open your team's projects</div>
        </div>
        {user?.is_admin && (
          <button className="btn btn-primary" onClick={() => setShowCreate(true)}>
            + Create project
          </button>
        )}
      </div>

      {my && my.total_projects > 0 && (
        <div className="my-insights">
          <div className="insights-stats">
            <StatCard label="Your projects" value={my.total_projects} accent="indigo" />
            <StatCard label="Issues" value={my.total_issues} accent="slate" />
            <StatCard label="Open" value={my.open_issues} accent="amber" />
            <StatCard label="Resolution rate" value={`${Math.round((my.resolution_rate || 0) * 100)}%`} accent="green" />
          </div>
          {(my.total_overdue > 0 || my.total_high_priority_open > 0 || my.projects_needing_attention > 0) && (
            <div className="my-attn-line">
              {my.total_overdue > 0 && <span className="my-attn-part sev-high">{my.total_overdue} overdue</span>}
              {my.total_high_priority_open > 0 && (
                <span className="my-attn-part sev-medium">{my.total_high_priority_open} high-priority</span>
              )}
              {my.projects_needing_attention > 0 && (
                <span className="my-attn-part">
                  {my.projects_needing_attention} project{my.projects_needing_attention === 1 ? '' : 's'} need attention
                </span>
              )}
              <span className="muted">across your projects</span>
              {my.top_attention.slice(0, 3).map((i) => (
                <Link key={i.key} to={`/browse/${i.key}`} className="my-attn-chip" title={i.summary}>
                  {i.key}
                </Link>
              ))}
            </div>
          )}

          {topProjects.length > 0 && (
            <div className="my-insights-top">
              <span className="my-insights-top-label">Most active:</span>
              {topProjects.map((p) => (
                <Link key={p.project_id} to={`/projects/${p.project_key}/insights`} className="my-insights-chip">
                  <span className="color-dot" style={{ background: colorFor(p.project_key, p.avatar_color) }} />
                  {p.project_key}
                  <span className="muted">{p.total_issues}</span>
                </Link>
              ))}
            </div>
          )}
        </div>
      )}

      {loading ? (
        <SpinnerCenter />
      ) : projects.length === 0 ? (
        <EmptyState
          icon="🗂"
          title="No projects yet"
          message={user?.is_admin ? 'Create your first project to get started.' : 'Ask an admin to add you to a project.'}
          action={user?.is_admin && <button className="btn btn-primary" onClick={() => setShowCreate(true)}>+ Create project</button>}
        />
      ) : (
        <div className="project-grid">
          {projects.map((p) => (
            <Link key={p.id} to={`/projects/${p.key}/board`} className="project-card">
              <div className="project-card-head">
                <span className="project-avatar" style={{ background: colorFor(p.key, p.avatar_color) }}>
                  {p.key.slice(0, 2)}
                </span>
                <div>
                  <div className="pkey">{p.key}</div>
                  <div className="pname">{p.name}</div>
                </div>
              </div>
              <span className="ptype">{p.project_type}</span>
            </Link>
          ))}
        </div>
      )}

      {showCreate && (
        <CreateProjectModal
          onClose={() => setShowCreate(false)}
          onCreated={() => {
            setShowCreate(false);
            load();
          }}
        />
      )}
    </div>
  );
}

function CreateProjectModal({ onClose, onCreated }: { onClose: () => void; onCreated: () => void }) {
  const navigate = useNavigate();
  const [name, setName] = useState('');
  const [key, setKey] = useState('');
  const [keyTouched, setKeyTouched] = useState(false);
  const [description, setDescription] = useState('');
  const [type, setType] = useState('scrum');
  const [color, setColor] = useState(COLORS[0]);
  const [lead, setLead] = useState<User | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  function onName(v: string) {
    setName(v);
    if (!keyTouched) {
      setKey(
        v
          .replace(/[^a-zA-Z]/g, '')
          .slice(0, 4)
          .toUpperCase()
      );
    }
  }

  async function submit() {
    setError('');
    if (!name.trim()) return setError('Name is required');
    if (!key.trim()) return setError('Key is required');
    setBusy(true);
    try {
      const proj = await createProject({
        name: name.trim(),
        key: key.trim().toUpperCase(),
        description: description || undefined,
        project_type: type,
        avatar_color: color,
        lead_id: lead?.id,
      });
      onCreated();
      navigate(`/projects/${proj.key}/board`);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create project'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      onClose={onClose}
      title="Create project"
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={busy}>
            {busy ? 'Creating…' : 'Create'}
          </button>
        </>
      }
    >
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row gap-16 wrap">
        <div className="field flex-1" style={{ minWidth: 220 }}>
          <label>Name</label>
          <input className="input" autoFocus value={name} onChange={(e) => onName(e.target.value)} placeholder="Engineering" />
        </div>
        <div className="field" style={{ width: 120 }}>
          <label>Key</label>
          <input
            className="input"
            value={key}
            onChange={(e) => {
              setKeyTouched(true);
              setKey(e.target.value.toUpperCase());
            }}
            maxLength={10}
            placeholder="ENG"
          />
        </div>
      </div>
      <div className="field">
        <label>Description</label>
        <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="row gap-16 wrap">
        <div className="field flex-1" style={{ minWidth: 160 }}>
          <label>Type</label>
          <select className="select" value={type} onChange={(e) => setType(e.target.value)}>
            <option value="scrum">Scrum</option>
            <option value="kanban">Kanban</option>
          </select>
        </div>
        <div className="field flex-1" style={{ minWidth: 200 }}>
          <label>Lead</label>
          <UserPicker value={lead} onChange={setLead} allowUnassigned={false} />
        </div>
      </div>
      <div className="field">
        <label>Avatar color</label>
        <div className="row gap-8 wrap">
          {COLORS.map((c) => (
            <button
              key={c}
              type="button"
              onClick={() => setColor(c)}
              style={{
                width: 28,
                height: 28,
                borderRadius: 7,
                background: c,
                border: color === c ? '3px solid #0f172a' : '2px solid #fff',
                boxShadow: '0 0 0 1px #e2e8f0',
              }}
            />
          ))}
        </div>
      </div>
    </Modal>
  );
}
