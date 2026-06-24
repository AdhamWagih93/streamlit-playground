import { useEffect, useState } from 'react';
import { Role, ProjectActor, Group, User } from '../../types';
import {
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
  canAdmin: boolean;
  setError: (s: string) => void;
  onGoToPermissions: () => void;
}

export function PeopleTab({ projectId, canAdmin, setError, onGoToPermissions }: Props) {
  const [roles, setRoles] = useState<Role[]>([]);
  const [actors, setActors] = useState<ProjectActor[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);

  function loadActors() {
    listProjectActors(projectId).then(setActors).catch((e) => setError(apiErrorMessage(e)));
  }

  useEffect(() => {
    Promise.all([listRoles(), listProjectActors(projectId), listGroups()])
      .then(([r, a, g]) => {
        setRoles(r);
        setActors(a);
        setGroups(g);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, [projectId]);

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="section-head">
        <h2 className="section-head-title">People &amp; roles</h2>
        <p className="section-head-sub">
          People are the users and groups who can access this project. Their role determines what
          they're allowed to do.
        </p>
      </div>

      <div className="callout callout-info">
        Want to know what each role can do?{' '}
        <button className="link-btn" onClick={onGoToPermissions}>See the Permissions tab</button>.
      </div>

      {roles.map((role) => (
        <RoleGroup
          key={role.id}
          projectId={projectId}
          role={role}
          actors={actors.filter((a) => a.role_id === role.id)}
          groups={groups}
          canAdmin={canAdmin}
          onChanged={loadActors}
          setError={setError}
        />
      ))}
    </div>
  );
}

function RoleGroup({
  projectId,
  role,
  actors,
  groups,
  canAdmin,
  onChanged,
  setError,
}: {
  projectId: string;
  role: Role;
  actors: ProjectActor[];
  groups: Group[];
  canAdmin: boolean;
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
      setError(apiErrorMessage(e, 'Could not add to role'));
    }
  }

  async function remove(actor: ProjectActor) {
    await removeProjectActor(projectId, actor.id).catch((e) => setError(apiErrorMessage(e)));
    onChanged();
  }

  return (
    <div className="role-group">
      <div className="role-group-head">
        <div className="row gap-8">
          <span className="role-badge">{role.name}</span>
          {role.is_default && <span className="badge badge-info">default</span>}
          <span className="muted text-sm">{actors.length} {actors.length === 1 ? 'member' : 'members'}</span>
        </div>
      </div>
      {role.description && <div className="muted text-sm mb-8">{role.description}</div>}

      <div className="people-list">
        {actors.length === 0 && <span className="muted text-sm">No one assigned to this role yet.</span>}
        {actors.map((a) => (
          <span className={`person-chip ${a.group ? 'is-group' : ''}`} key={a.id}>
            {a.user ? (
              <>
                <Avatar name={a.user.display_name} size={20} />
                <span className="person-name">{a.user.display_name}</span>
              </>
            ) : (
              <>
                <span className="group-glyph">⛂</span>
                <span className="person-name">{a.group?.name}</span>
              </>
            )}
            {canAdmin && (
              <button className="person-remove" title="Remove" onClick={() => remove(a)}>×</button>
            )}
          </span>
        ))}
      </div>

      {canAdmin && (
        <div className="row gap-8 wrap mt-8" style={{ alignItems: 'flex-end' }}>
          <select className="select" value={mode} onChange={(e) => setMode(e.target.value as 'user' | 'group')} style={{ width: 110 }}>
            <option value="user">Person</option>
            <option value="group">Group</option>
          </select>
          {mode === 'user' ? (
            <div className="flex-1" style={{ minWidth: 200 }}>
              <UserPicker value={user} onChange={setUser} allowUnassigned={false} placeholder={`Add a person to ${role.name}…`} />
            </div>
          ) : (
            <select className="select flex-1" value={groupId} onChange={(e) => setGroupId(e.target.value)} style={{ minWidth: 200 }}>
              <option value="">Select a group…</option>
              {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
            </select>
          )}
          <button className="btn btn-primary" onClick={add} disabled={mode === 'user' ? !user : !groupId}>
            Add
          </button>
        </div>
      )}
    </div>
  );
}
