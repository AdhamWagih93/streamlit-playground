import { useEffect, useState } from 'react';
import { GlobalPermission, PermissionCatalog, Group, User } from '../../types';
import {
  listGlobalPermissions,
  createGlobalPermission,
  deleteGlobalPermission,
} from '../../api/admin';
import { getPermissionCatalog, listGroups } from '../../api/rbac';
import { SpinnerCenter } from '../../components/Spinner';
import { EmptyState } from '../../components/EmptyState';
import { UserPicker } from '../../components/UserPicker';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function GlobalPermissions() {
  const [items, setItems] = useState<GlobalPermission[]>([]);
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<ToastMsg | null>(null);

  const [permission, setPermission] = useState('');
  const [holderType, setHolderType] = useState<'group' | 'user'>('group');
  const [groupId, setGroupId] = useState('');
  const [user, setUser] = useState<User | null>(null);

  function load() {
    listGlobalPermissions().then(setItems).catch((e) => setError(apiErrorMessage(e)));
  }

  useEffect(() => {
    Promise.all([listGlobalPermissions(), getPermissionCatalog(), listGroups()])
      .then(([perms, cat, grps]) => {
        setItems(perms);
        setCatalog(cat);
        setGroups(grps);
        if (cat.global_permissions[0]) setPermission(cat.global_permissions[0].key);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  async function add() {
    const holderValue = holderType === 'group' ? groupId : user?.id || '';
    if (!permission || !holderValue) return;
    try {
      await createGlobalPermission({ permission, holder_type: holderType, holder_value: holderValue });
      setGroupId('');
      setUser(null);
      load();
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Could not add grant') });
    }
  }

  async function remove(g: GlobalPermission) {
    await deleteGlobalPermission(g.id).catch((e) => setToast({ ok: false, text: apiErrorMessage(e) }));
    load();
  }

  function describe(perm: string): string {
    return catalog?.global_permissions.find((p) => p.key === perm)?.description || perm;
  }
  function holderLabel(g: GlobalPermission): string {
    if (g.holder_type === 'group') return groups.find((x) => x.id === g.holder_value)?.name || g.holder_value;
    return g.holder_value;
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 760 }}>
      <h2 className="page-title" style={{ fontSize: 19 }}>Global Permissions</h2>
      <p className="muted text-sm mb-16">Site-wide permissions granted to groups or users.</p>
      {error && <div className="alert alert-error">{error}</div>}

      <div className="section-card">
        <h3>Add grant</h3>
        <div className="row gap-8 wrap mt-8" style={{ alignItems: 'flex-end' }}>
          <div className="field" style={{ marginBottom: 0, minWidth: 200 }}>
            <label>Permission</label>
            <select className="select" value={permission} onChange={(e) => setPermission(e.target.value)}>
              {catalog?.global_permissions.map((p) => (
                <option key={p.key} value={p.key}>{p.description}</option>
              ))}
            </select>
          </div>
          <div className="field" style={{ marginBottom: 0, width: 130 }}>
            <label>Holder</label>
            <select className="select" value={holderType} onChange={(e) => setHolderType(e.target.value as 'group' | 'user')}>
              <option value="group">Group</option>
              <option value="user">User</option>
            </select>
          </div>
          {holderType === 'group' ? (
            <div className="field" style={{ marginBottom: 0, minWidth: 180 }}>
              <label>Group</label>
              <select className="select" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
                <option value="">Select group…</option>
                {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
              </select>
            </div>
          ) : (
            <div className="field" style={{ marginBottom: 0, minWidth: 200 }}>
              <label>User</label>
              <UserPicker value={user} onChange={setUser} allowUnassigned={false} placeholder="Pick a user…" />
            </div>
          )}
          <button className="btn btn-primary" onClick={add} disabled={!permission || (holderType === 'group' ? !groupId : !user)}>Add</button>
        </div>
      </div>

      {items.length === 0 ? (
        <EmptyState icon="🛡️" title="No global grants" message="Add a grant to give site-wide permissions." />
      ) : (
        <table className="data-table">
          <thead><tr><th>Permission</th><th style={{ width: 90 }}>Holder</th><th>Value</th><th style={{ width: 60 }}></th></tr></thead>
          <tbody>
            {items.map((g) => (
              <tr key={g.id}>
                <td>{describe(g.permission)}</td>
                <td style={{ textTransform: 'capitalize' }}>{g.holder_type}</td>
                <td>{holderLabel(g)}</td>
                <td><button className="btn btn-ghost btn-sm" onClick={() => remove(g)}>×</button></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}
