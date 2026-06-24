import { useEffect, useState } from 'react';
import {
  PermissionScheme,
  PermissionSchemeDetail,
  PermissionCatalog,
  PermissionGrant,
  HolderType,
  Group,
  Role,
  User,
} from '../../types';
import {
  listPermissionSchemes,
  createPermissionScheme,
  getPermissionScheme,
  deletePermissionScheme,
  addSchemeGrant,
  deleteSchemeGrant,
  getPermissionCatalog,
  listGroups,
  listRoles,
} from '../../api/rbac';
import { Modal } from '../../components/Modal';
import { SpinnerCenter } from '../../components/Spinner';
import { EmptyState } from '../../components/EmptyState';
import { UserPicker } from '../../components/UserPicker';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function PermissionSchemes() {
  const [items, setItems] = useState<PermissionScheme[]>([]);
  const [catalog, setCatalog] = useState<PermissionCatalog | null>(null);
  const [groups, setGroups] = useState<Group[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [editingId, setEditingId] = useState<string | null>(null);

  function loadList() {
    listPermissionSchemes().then(setItems).catch((e) => setError(apiErrorMessage(e)));
  }

  useEffect(() => {
    Promise.all([listPermissionSchemes(), getPermissionCatalog(), listGroups(), listRoles()])
      .then(([schemes, cat, grps, rls]) => {
        setItems(schemes);
        setCatalog(cat);
        setGroups(grps);
        setRoles(rls);
      })
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }, []);

  async function create() {
    if (!name.trim()) return;
    try {
      const s = await createPermissionScheme({ name: name.trim(), description: description || undefined });
      setName('');
      setDescription('');
      setCreating(false);
      loadList();
      setEditingId(s.id);
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Could not create scheme') });
    }
  }

  async function remove(s: PermissionScheme) {
    if (!confirm(`Delete scheme “${s.name}”?`)) return;
    await deletePermissionScheme(s.id).catch((e) => setToast({ ok: false, text: apiErrorMessage(e) }));
    loadList();
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 820 }}>
      <div className="row-between mb-16">
        <div>
          <h2 className="page-title" style={{ fontSize: 19 }}>Permission Schemes</h2>
          <p className="muted text-sm">Reusable sets of project permission grants.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ Create scheme</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      {items.length === 0 ? (
        <EmptyState icon="🗂️" title="No permission schemes" message="Create a scheme to manage project permissions." />
      ) : (
        items.map((s) => (
          <div className="list-row" key={s.id}>
            <div className="list-row-main">
              <div className="row gap-8">
                <span className="list-row-title">{s.name}</span>
                {s.is_default && <span className="badge badge-info">default</span>}
              </div>
              {s.description && <div className="muted text-xs">{s.description}</div>}
            </div>
            <div className="row gap-8">
              <button className="btn btn-sm" onClick={() => setEditingId(s.id)}>Edit grants</button>
              <button className="btn btn-ghost btn-sm" onClick={() => remove(s)}>×</button>
            </div>
          </div>
        ))
      )}

      {creating && (
        <Modal
          open title="Create permission scheme" onClose={() => setCreating(false)}
          footer={<><button className="btn" onClick={() => setCreating(false)}>Cancel</button><button className="btn btn-primary" onClick={create} disabled={!name.trim()}>Create</button></>}
        >
          <div className="field"><label>Name</label><input className="input" autoFocus value={name} onChange={(e) => setName(e.target.value)} /></div>
          <div className="field"><label>Description</label><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        </Modal>
      )}

      {editingId && catalog && (
        <SchemeEditor
          schemeId={editingId}
          catalog={catalog}
          groups={groups}
          roles={roles}
          onClose={() => setEditingId(null)}
          onError={(t) => setToast({ ok: false, text: t })}
        />
      )}

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

function SchemeEditor({
  schemeId,
  catalog,
  groups,
  roles,
  onClose,
  onError,
}: {
  schemeId: string;
  catalog: PermissionCatalog;
  groups: Group[];
  roles: Role[];
  onClose: () => void;
  onError: (t: string) => void;
}) {
  const [scheme, setScheme] = useState<PermissionSchemeDetail | null>(null);

  // add-grant form
  const [permission, setPermission] = useState(catalog.project_permissions[0]?.key || '');
  const [holderType, setHolderType] = useState<HolderType>('role');
  const [roleId, setRoleId] = useState('');
  const [groupId, setGroupId] = useState('');
  const [user, setUser] = useState<User | null>(null);
  const [special, setSpecial] = useState(catalog.special_holders[0] || '');

  function load() {
    getPermissionScheme(schemeId).then(setScheme).catch((e) => onError(apiErrorMessage(e)));
  }
  useEffect(load, [schemeId]);

  const holderTypes: HolderType[] = (catalog.holder_types.length
    ? catalog.holder_types
    : ['role', 'group', 'user', 'special']) as HolderType[];

  function holderValue(): string {
    switch (holderType) {
      case 'role': return roleId;
      case 'group': return groupId;
      case 'user': return user?.id || '';
      case 'special': return special;
    }
  }

  async function add() {
    const hv = holderValue();
    if (!permission || !hv) return;
    try {
      await addSchemeGrant(schemeId, { permission, holder_type: holderType, holder_value: hv });
      setRoleId('');
      setGroupId('');
      setUser(null);
      load();
    } catch (e) {
      onError(apiErrorMessage(e, 'Could not add grant'));
    }
  }

  async function removeGrant(g: PermissionGrant) {
    await deleteSchemeGrant(schemeId, g.id).catch((e) => onError(apiErrorMessage(e)));
    load();
  }

  function describePerm(key: string): string {
    return catalog.project_permissions.find((p) => p.key === key)?.description || key;
  }
  function holderText(g: PermissionGrant): string {
    if (g.holder_type === 'role') return `Role: ${roles.find((r) => r.id === g.holder_value)?.name || g.holder_value}`;
    if (g.holder_type === 'group') return `Group: ${groups.find((x) => x.id === g.holder_value)?.name || g.holder_value}`;
    if (g.holder_type === 'user') return `User: ${g.holder_value}`;
    return `Special: ${g.holder_value}`;
  }

  // group grants by permission
  const byPerm: Record<string, PermissionGrant[]> = {};
  (scheme?.grants || []).forEach((g) => {
    (byPerm[g.permission] ||= []).push(g);
  });

  return (
    <Modal open title={scheme ? scheme.name : 'Permission scheme'} onClose={onClose} size="wide">
      {!scheme ? (
        <SpinnerCenter />
      ) : (
        <>
          <div className="section-card">
            <h3>Add grant</h3>
            <div className="row gap-8 wrap mt-8" style={{ alignItems: 'flex-end' }}>
              <div className="field" style={{ marginBottom: 0, minWidth: 200 }}>
                <label>Permission</label>
                <select className="select" value={permission} onChange={(e) => setPermission(e.target.value)}>
                  {catalog.project_permissions.map((p) => <option key={p.key} value={p.key}>{p.description}</option>)}
                </select>
              </div>
              <div className="field" style={{ marginBottom: 0, width: 120 }}>
                <label>Holder type</label>
                <select className="select" value={holderType} onChange={(e) => setHolderType(e.target.value as HolderType)}>
                  {holderTypes.map((h) => <option key={h} value={h} style={{ textTransform: 'capitalize' }}>{h}</option>)}
                </select>
              </div>
              {holderType === 'role' && (
                <div className="field" style={{ marginBottom: 0, minWidth: 160 }}>
                  <label>Role</label>
                  <select className="select" value={roleId} onChange={(e) => setRoleId(e.target.value)}>
                    <option value="">Select role…</option>
                    {roles.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
                  </select>
                </div>
              )}
              {holderType === 'group' && (
                <div className="field" style={{ marginBottom: 0, minWidth: 160 }}>
                  <label>Group</label>
                  <select className="select" value={groupId} onChange={(e) => setGroupId(e.target.value)}>
                    <option value="">Select group…</option>
                    {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                  </select>
                </div>
              )}
              {holderType === 'user' && (
                <div className="field" style={{ marginBottom: 0, minWidth: 200 }}>
                  <label>User</label>
                  <UserPicker value={user} onChange={setUser} allowUnassigned={false} placeholder="Pick a user…" />
                </div>
              )}
              {holderType === 'special' && (
                <div className="field" style={{ marginBottom: 0, minWidth: 160 }}>
                  <label>Special</label>
                  <select className="select" value={special} onChange={(e) => setSpecial(e.target.value)}>
                    {catalog.special_holders.map((s) => <option key={s} value={s}>{s}</option>)}
                  </select>
                </div>
              )}
              <button className="btn btn-primary" onClick={add} disabled={!permission || !holderValue()}>Add</button>
            </div>
          </div>

          {Object.keys(byPerm).length === 0 ? (
            <div className="muted text-sm">No grants yet.</div>
          ) : (
            Object.entries(byPerm).map(([perm, grants]) => (
              <div className="section-card" key={perm}>
                <h3>{describePerm(perm)}</h3>
                <div className="chip-row mt-8">
                  {grants.map((g) => (
                    <span className="token" key={g.id}>
                      {holderText(g)}
                      <button onClick={() => removeGrant(g)}>×</button>
                    </span>
                  ))}
                </div>
              </div>
            ))
          )}
        </>
      )}
    </Modal>
  );
}
