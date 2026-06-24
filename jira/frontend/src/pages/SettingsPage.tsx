import { useEffect, useState } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  ProjectOut,
  Component,
  Version,
  User,
  Role,
} from '../types';
import {
  getProject,
  updateProject,
  deleteProject,
  listComponents,
  createComponent,
  deleteComponent,
  listVersions,
  createVersion,
  updateVersion,
  deleteVersion,
} from '../api/projects';
import { listRoles, listProjectActors } from '../api/rbac';
import { UserPicker } from '../components/UserPicker';
import { SpinnerCenter } from '../components/Spinner';
import { useAuth } from '../store/auth';
import { formatDate } from '../lib/format';
import { apiErrorMessage } from '../api/client';
import { PeopleTab } from './settings/PeopleTab';
import { PermissionsTab } from './settings/PermissionsTab';
import { JiraSyncTab } from './settings/JiraSyncTab';

type Tab = 'details' | 'people' | 'permissions' | 'components' | 'versions' | 'jira sync';

const TABS: Tab[] = ['details', 'people', 'permissions', 'components', 'versions', 'jira sync'];

export function SettingsPage() {
  const { projectKey } = useParams();
  const navigate = useNavigate();
  const me = useAuth((s) => s.user);
  const [project, setProject] = useState<ProjectOut | null>(null);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>('details');
  const [error, setError] = useState('');
  const [canAdmin, setCanAdmin] = useState(false);

  function reload() {
    if (!projectKey) return;
    return getProject(projectKey)
      .then(setProject)
      .catch((e) => setError(apiErrorMessage(e)));
  }

  useEffect(() => {
    if (!projectKey) return;
    setLoading(true);
    getProject(projectKey)
      .then(async (p) => {
        setProject(p);
        // Compute project-admin capability.
        let admin = !!me?.is_admin || p.lead?.id === me?.id;
        if (!admin && me) {
          try {
            const [roles, actors]: [Role[], Awaited<ReturnType<typeof listProjectActors>>] = await Promise.all([
              listRoles(),
              listProjectActors(p.id),
            ]);
            const adminRole = roles.find((r) => /admin/i.test(r.name));
            if (adminRole) {
              admin = actors.some((a) => a.role_id === adminRole.id && a.user?.id === me.id);
            }
          } catch {
            /* keep admin=false on failure */
          }
        }
        setCanAdmin(admin);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, [projectKey, me]);

  if (loading) return <SpinnerCenter />;
  if (!project) return <div className="page"><div className="alert alert-error">{error || 'Project not found'}</div></div>;

  return (
    <div className="page">
      <div className="breadcrumb">{project.key} / Settings</div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{project.name} settings</h1>
          <div className="page-subtitle">
            {canAdmin
              ? 'You can manage this project.'
              : 'You can view these settings. Some actions require a project admin.'}
          </div>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="tabs">
        {TABS.map((t) => (
          <button key={t} className={`tab ${tab === t ? 'active' : ''}`} onClick={() => setTab(t)} style={{ textTransform: 'capitalize' }}>
            {t}
          </button>
        ))}
      </div>

      {tab === 'details' && <DetailsTab project={project} onSaved={setProject} canAdmin={canAdmin} onDeleted={() => navigate('/projects')} setError={setError} />}
      {tab === 'people' && <PeopleTab projectId={project.id} canAdmin={canAdmin} setError={setError} onGoToPermissions={() => setTab('permissions')} />}
      {tab === 'permissions' && <PermissionsTab project={project} canAdmin={canAdmin} setError={setError} onSchemeChanged={() => reload()} />}
      {tab === 'components' && <ComponentsTab projectId={project.id} canAdmin={canAdmin} setError={setError} />}
      {tab === 'versions' && <VersionsTab projectId={project.id} canAdmin={canAdmin} setError={setError} />}
      {tab === 'jira sync' && <JiraSyncTab projectId={project.id} isAdmin={canAdmin} setError={setError} />}
    </div>
  );
}

function DetailsTab({
  project,
  onSaved,
  canAdmin,
  onDeleted,
  setError,
}: {
  project: ProjectOut;
  onSaved: (p: ProjectOut) => void;
  canAdmin: boolean;
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
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} disabled={!canAdmin} />
      </div>
      <div className="field">
        <label>Key</label>
        <input className="input" value={project.key} disabled />
      </div>
      <div className="field">
        <label>Description</label>
        <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} disabled={!canAdmin} />
      </div>
      <div className="field">
        <label>Project lead</label>
        <UserPicker value={lead} onChange={setLead} allowUnassigned={false} disabled={!canAdmin} />
      </div>
      {canAdmin && (
        <button className="btn btn-primary" onClick={save} disabled={busy}>
          {busy ? 'Saving…' : 'Save changes'}
        </button>
      )}

      {canAdmin && (
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

function ComponentsTab({ projectId, canAdmin, setError }: { projectId: string; canAdmin: boolean; setError: (s: string) => void }) {
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
      {canAdmin && (
        <div className="row gap-8 mb-16 wrap">
          <input className="input flex-1" placeholder="Component name" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
          <input className="input flex-1" placeholder="Description (optional)" value={description} onChange={(e) => setDescription(e.target.value)} style={{ minWidth: 160 }} />
          <button className="btn btn-primary" onClick={add} disabled={!name.trim()}>
            Add
          </button>
        </div>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th>Description</th>
            {canAdmin && <th style={{ width: 60 }}></th>}
          </tr>
        </thead>
        <tbody>
          {items.map((c) => (
            <tr key={c.id}>
              <td>{c.name}</td>
              <td className="muted">{c.description}</td>
              {canAdmin && (
                <td>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={async () => {
                      await deleteComponent(projectId, c.id).catch((e) => setError(apiErrorMessage(e)));
                      load();
                    }}
                  >
                    ×
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <div className="muted text-sm mt-8">No components yet.</div>}
    </div>
  );
}

function VersionsTab({ projectId, canAdmin, setError }: { projectId: string; canAdmin: boolean; setError: (s: string) => void }) {
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
      {canAdmin && (
        <div className="row gap-8 mb-16 wrap">
          <input className="input flex-1" placeholder="Version name e.g. 1.0.0" value={name} onChange={(e) => setName(e.target.value)} style={{ minWidth: 160 }} />
          <input className="input" type="date" value={date} onChange={(e) => setDate(e.target.value)} style={{ width: 160 }} />
          <button className="btn btn-primary" onClick={add} disabled={!name.trim()}>
            Add
          </button>
        </div>
      )}
      <table className="data-table">
        <thead>
          <tr>
            <th>Name</th>
            <th style={{ width: 130 }}>Release date</th>
            <th style={{ width: 120 }}>Status</th>
            {canAdmin && <th style={{ width: 60 }}></th>}
          </tr>
        </thead>
        <tbody>
          {items.map((v) => (
            <tr key={v.id}>
              <td>{v.name}</td>
              <td className="muted">{formatDate(v.release_date)}</td>
              <td>
                <button
                  className={`status-badge status-${v.released ? 'done' : 'todo'} ${canAdmin ? 'pointer' : ''}`}
                  style={{ border: 'none' }}
                  disabled={!canAdmin}
                  onClick={async () => {
                    if (!canAdmin) return;
                    await updateVersion(projectId, v.id, { released: !v.released }).catch((e) => setError(apiErrorMessage(e)));
                    load();
                  }}
                >
                  {v.released ? 'Released' : 'Unreleased'}
                </button>
              </td>
              {canAdmin && (
                <td>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={async () => {
                      await deleteVersion(projectId, v.id).catch((e) => setError(apiErrorMessage(e)));
                      load();
                    }}
                  >
                    ×
                  </button>
                </td>
              )}
            </tr>
          ))}
        </tbody>
      </table>
      {items.length === 0 && <div className="muted text-sm mt-8">No versions yet.</div>}
    </div>
  );
}
