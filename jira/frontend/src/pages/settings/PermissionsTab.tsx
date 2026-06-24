import { useEffect, useState } from 'react';
import { PermissionScheme, Role, ProjectActor, Group, User } from '../../types';
import {
  listPermissionSchemes,
  setProjectScheme,
  listRoles,
  listProjectActors,
  addProjectActor,
  removeProjectActor,
  listGroups,
} from '../../api/rbac';
import { SpinnerCenter } from '../../components/Spinner';
import { UserPicker } from '../../components/UserPicker';
import { Avatar } from '../../components/Avatar';
import { apiErrorMessage } from '../../api/client';

interface Props {
  projectId: string;
  currentSchemeId?: string | null;
  setError: (s: string) => void;
}

export function PermissionsTab({ projectId, currentSchemeId, setError }: Props) {
  const [schemes, setSchemes] = useState<PermissionScheme[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [actors, setActors] = useState<ProjectActor[]>([]);
  const [schemeId, setSchemeId] = useState<string>(currentSchemeId || '');
  const [loading, setLoading] = useState(true);
  const [savingScheme, setSavingScheme] = useState(false);

  function loadActors() {
    listProjectActors(projectId).then(setActors).catch((e) => setError(apiErrorMessage(e)));
  }

  useEffect(() => {
    Promise.all([listPermissionSchemes(), listRoles(), listProjectActors(projectId), listGroups()])
      .then(([s, r, a, g]) => {
        setSchemes(s);
        setRoles(r);
        setActors(a);
        setGroups(g);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  async function saveScheme() {
    setSavingScheme(true);
    try {
      await setProjectScheme(projectId, schemeId || null);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not assign scheme'));
    } finally {
      setSavingScheme(false);
    }
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 720 }}>
      <div className="section-card">
        <h3>Permission scheme</h3>
        <p className="muted text-sm mb-8">The scheme controls who can perform actions in this project.</p>
        <div className="row gap-8 wrap" style={{ alignItems: 'flex-end' }}>
          <select className="select" value={schemeId} onChange={(e) => setSchemeId(e.target.value)} style={{ minWidth: 240 }}>
            <option value="">Default permissions</option>
            {schemes.map((s) => (
              <option key={s.id} value={s.id}>{s.name}{s.is_default ? ' (default)' : ''}</option>
            ))}
          </select>
          <button className="btn btn-primary" onClick={saveScheme} disabled={savingScheme}>
            {savingScheme ? 'Saving…' : 'Apply scheme'}
          </button>
        </div>
      </div>

      <h3 style={{ fontSize: 16, margin: '16px 0 8px' }}>Project roles</h3>
      <p className="muted text-sm mb-16">Assign users and groups to roles for this project.</p>

      {roles.map((role) => (
        <RoleRow
          key={role.id}
          projectId={projectId}
          role={role}
          actors={actors.filter((a) => a.role_id === role.id)}
          groups={groups}
          onChanged={loadActors}
          setError={setError}
        />
      ))}
    </div>
  );
}

function RoleRow({
  projectId,
  role,
  actors,
  groups,
  onChanged,
  setError,
}: {
  projectId: string;
  role: Role;
  actors: ProjectActor[];
  groups: Group[];
  onChanged: () => void;
  setError: (s: string) => void;
}) {
  const [mode, setMode] = useState<'user' | 'group'>('user');
  const [user, setUser] = useState<User | null>(null);
  const [groupId, setGroupId] = useState('');

  async function add() {
    try {
      if (mode === 'user') {
        if (!user) return;
        await addProjectActor(projectId, { role_id: role.id, user_id: user.id });
        setUser(null);
      } else {
        if (!groupId) return;
        await addProjectActor(projectId, { role_id: role.id, group_id: groupId });
        setGroupId('');
      }
      onChanged();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not add actor'));
    }
  }

  async function remove(actor: ProjectActor) {
    await removeProjectActor(projectId, actor.id).catch((e) => setError(apiErrorMessage(e)));
    onChanged();
  }

  return (
    <div className="section-card">
      <div className="row-between">
        <h3>{role.name}{role.is_default && <span className="badge badge-info" style={{ marginLeft: 8 }}>default</span>}</h3>
      </div>
      {role.description && <div className="muted text-xs mb-8">{role.description}</div>}

      <div className="chip-row mb-8">
        {actors.length === 0 && <span className="muted text-sm">No one assigned.</span>}
        {actors.map((a) => (
          <span className="token" key={a.id}>
            {a.user ? (
              <span className="row gap-8"><Avatar name={a.user.display_name} size={18} /> {a.user.display_name}</span>
            ) : (
              <span>👥 {a.group?.name}</span>
            )}
            <button onClick={() => remove(a)}>×</button>
          </span>
        ))}
      </div>

      <div className="row gap-8 wrap" style={{ alignItems: 'flex-end' }}>
        <select className="select" value={mode} onChange={(e) => setMode(e.target.value as 'user' | 'group')} style={{ width: 110 }}>
          <option value="user">User</option>
          <option value="group">Group</option>
        </select>
        {mode === 'user' ? (
          <div className="flex-1" style={{ minWidth: 200 }}>
            <UserPicker value={user} onChange={setUser} allowUnassigned={false} placeholder="Add a person…" />
          </div>
        ) : (
          <select className="select flex-1" value={groupId} onChange={(e) => setGroupId(e.target.value)} style={{ minWidth: 180 }}>
            <option value="">Select group…</option>
            {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
          </select>
        )}
        <button className="btn btn-primary" onClick={add} disabled={mode === 'user' ? !user : !groupId}>Add</button>
      </div>
    </div>
  );
}
