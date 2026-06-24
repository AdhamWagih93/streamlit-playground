import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ProjectOut,
  ProjectMember,
  Component,
  Version,
  User,
} from '../types';
import {
  getProject,
  updateProject,
  deleteProject,
  listMembers,
  addMember,
  removeMember,
  listComponents,
  createComponent,
  deleteComponent,
  listVersions,
  createVersion,
  updateVersion,
  deleteVersion,
} from '../api/projects';
import { UserPicker } from '../components/UserPicker';
import { Avatar } from '../components/Avatar';
import { SpinnerCenter } from '../components/Spinner';
import { useAuth } from '../store/auth';
import { formatDate } from '../lib/format';
import { apiErrorMessage } from '../api/client';
import { PermissionsTab } from './settings/PermissionsTab';
import { JiraSyncTab } from './settings/JiraSyncTab';

type Tab = 'details' | 'members' | 'components' | 'versions' | 'permissions' | 'jira sync';

export function SettingsPage() {
  const { projectKey } = useParams();
  const navigate = useNavigate();
  const me = useAuth((s) => s.user);
  const [project, setProject] = useState<ProjectOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>('details');
  const [error, setError] = useState('');

  useEffect(() => {
    if (!projectKey) return;
    setLoading(true);
    getProject(projectKey)
      .then(setProject)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, [projectKey]);

  if (loading) return <SpinnerCenter />;
  if (!project) return <div className="page"><div className="alert alert-error">{error || 'Project not found'}</div></div>;

  return (
    <div className="page">
      <div className="breadcrumb">{project.key} / Settings</div>
      <div className="page-header">
        <h1 className="page-title">{project.name} settings</h1>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="tabs">
        {(['details', 'members', 'components', 'versions', 'permissions', 'jira sync'] as Tab[]).map((t) => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)} style={{ textTransform: 'capitalize' }}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'details' && <DetailsTab project={project} onSaved={setProject} isAdmin={!!me?.is_admin} onDeleted={() => navigate('/projects')} setError={setError} />}
      {tab === 'members' && <MembersTab projectId={project.id} setError={setError} />}
      {tab === 'components' && <ComponentsTab projectId={project.id} setError={setError} />}
      {tab === 'versions' && <VersionsTab projectId={project.id} setError={setError} />}
      {tab === 'permissions' && <PermissionsTab projectId={project.id} setError={setError} />}
      {tab === 'jira sync' && <JiraSyncTab projectId={project.id} isAdmin={!!me?.is_admin} setError={setError} />}
    </div>
  );
}

function DetailsTab({
  project,
  onSaved,
  isAdmin,
  onDeleted,
  setError,
}: {
  project: ProjectOut;
  onSaved: (p: ProjectOut) => void;
  isAdmin: boolean;
  onDeleted: () => void;
  setError: (s: string) => void;
}) {
  const [name, setName] = useState(project.name);
  const [description, setDescription] = useState(project.description || '');
  const [lead, setLead] = useState<User | null>(project.lead || null);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      const updated = await updateProject(project.id, { name, description, lead_id: lead?.id });
      onSaved(updated);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 560 }}>
      <div className="field">
        <label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label>Key</label>
        <input className="input" value={project.key} disabled />
      </div>
      <div className="field">
        <label>Description</label>
        <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} />
      </div>
      <div className="field">
        <label>Project lead</label>
        <UserPicker value={lead} onChange={setLead} allowUnassigned={false} />
      </div>
      <button className="btn btn-primary" onClick={save} disabled={busy}>
        {busy ? 'Saving…' : 'Save changes'}
      </button>

      {isAdmin && (
        <div className="mt-24" style={{ borderTop: '1px solid var(--border)', paddingTop: 16 }}>
          <h4 style={{ color: 'var(--red-500)' }}>Danger zone</h4>
          <button
            className="btn btn-danger"
            onClick={async () => {
              if (confirm(`Delete project ${project.key}? This cannot be undone.`)) {
                try {
                  await deleteProject(project.id);
                  onDeleted();
                } catch (e) {
                  setError(apiErrorMessage(e, 'Could not delete'));
                }
              }
            }}
          >
            Delete project
          </button>
        </div>
      )}
    </div>
  );
}

function MembersTab({ projectId, setError }: { projectId: string; setError: (s: string) => void }) {
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [picked, setPicked] = useState<User | null>(null);
  const [role, setRole] = useState('member');

  function load() {
    listMembers(projectId).then(setMembers).catch((e) => setError(apiErrorMessage(e)));
  }
  useEffect(load, [projectId]);

  async function add() {
    if (!picked) return;
    try {
      await addMember(projectId, picked.id, role);
      setPicked(null);
      load();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not add member'));
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="row gap-8 mb-16 wrap" style={{ alignItems: 'flex-end' }}>
        <div className="flex-1" style={{ minWidth: 200 }}>
          <UserPicker value={picked} onChange={setPicked} allowUnassigned={false} placeholder="Add a person…" />
        </div>
        <select className="select" style={{ width: 140 }} value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="member">Member</option>
          <option value="admin">Admin</option>
          <option value="viewer">Viewer</option>
        </select>
        <button className="btn btn-primary" onClick={add} disabled={!picked}>
          Add
        </button>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Member</th>
            <th style={{ width: 120 }}>Role</th>
            <th style={{ width: 80 }}></th>
          </tr>
        </thead>
        <tbody>
          {members.map((m) => (
            <tr key={m.user.id}>
              <td>
                <span className="row gap-8">
                  <Avatar user={m.user} size={26} /> {m.user.display_name}
                  <span className="text-xs muted">{m.user.email}</span>
                </span>
              </td>
              <td style={{ textTransform: 'capitalize' }}>{m.role}</td>
              <td>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={async (e) => {
                    e.stopPropagation();
                    await removeMember(projectId, m.user.id).catch(() => {});
                    load();
                  }}
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ComponentsTab({ projectId, setError }: { projectId: string; setError: (s: string) => void }) {
  const [items, setItems] = useState<Component[]>([]);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');

  function load() {
    listComponents(projectId).then(setItems).catch((e) => setError(apiErrorMessage(e)));
  }
  useEffect(load, [projectId]);

  async function add() {
    if (!name.trim()) return;
    try {
      await createComponent(projectId, { name: name.trim(), description: description || undefined });
      setName('');
      setDescription('');
      load();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create component'));
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="row gap-8 mb-16 wrap">
        <input className="input flex-1" placeholder="Component name" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
        <input className="input flex-1" placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} style={{ minWidth: 160 }} />
        <button className="btn btn-primary" onClick={add} disabled={!name.trim()}>
          Add
        </button>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Description</th>
            <th style={{ width: 80 }}></th>
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td className="muted">{c.description}</td>
              <td>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={async () => {
                    await deleteComponent(projectId, c.id).catch(() => {});
                    load();
                  }}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <div className="muted text-sm mt-8">No components yet.</div>}
    </div>
  );
}

function VersionsTab({ projectId, setError }: { projectId: string; setError: (s: string) => void }) {
  const [items, setItems] = useState<Version[]>([]);
  const [name, setName] = useState('');
  const [date, setDate] = useState('');

  function load() {
    listVersions(projectId).then(setItems).catch((e) => setError(apiErrorMessage(e)));
  }
  useEffect(load, [projectId]);

  async function add() {
    if (!name.trim()) return;
    try {
      await createVersion(projectId, { name: name.trim(), release_date: date || null, released: false });
      setName('');
      setDate('');
      load();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create version'));
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="row gap-8 mb-16 wrap">
        <input className="input flex-1" placeholder="Version name e.g. 1.0.0" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
        <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: 160 }} />
        <button className="btn btn-primary" onClick={add} disabled={!name.trim()}>
          Add
        </button>
      </div>
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th style={{ width: 130 }}>Release date</th>
            <th style={{ width: 120 }}>Status</th>
            <th style={{ width: 80 }}></th>
          </tr>
        </thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.id}>
              <td>{v.name}</td>
              <td className="muted">{formatDate(v.release_date)}</td>
              <td>
                <button
                  className={`status-badge status-${v.released ? 'done' : 'todo'} pointer`}
                  style={{ border: 'none' }}
                  onClick={async () => {
                    await updateVersion(projectId, v.id, { released: !v.released }).catch(() => {});
                    load();
                  }}
                >
                  {v.released ? 'Released' : 'Unreleased'}
                </button>
              </td>
              <td>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={async () => {
                    await deleteVersion(projectId, v.id).catch(() => {});
                    load();
                  }}
                >
                  ×
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <div className="muted text-sm mt-8">No versions yet.</div>}
    </div>
  );
}
