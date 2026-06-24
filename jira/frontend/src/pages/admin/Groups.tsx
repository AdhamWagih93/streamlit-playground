import { useEffect, useState } from 'react';
import { Group, GroupDetail, User } from '../../types';
import {
  listGroups,
  getGroup,
  createGroup,
  deleteGroup,
  addGroupMember,
  removeGroupMember,
} from '../../api/rbac';
import { Modal } from '../../components/Modal';
import { SpinnerCenter } from '../../components/Spinner';
import { EmptyState } from '../../components/EmptyState';
import { UserPicker } from '../../components/UserPicker';
import { Avatar } from '../../components/Avatar';
import { Toast, ToastMsg } from '../../components/Toast';
import { apiErrorMessage } from '../../api/client';

export function Groups() {
  const [items, setItems] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const [q, setQ] = useState('');
  const [creating, setCreating] = useState(false);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [detailId, setDetailId] = useState<string | null>(null);

  function load() {
    setLoading(true);
    listGroups(q)
      .then(setItems)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, [q]);

  async function create() {
    if (!name.trim()) return;
    try {
      await createGroup({ name: name.trim(), description: description || undefined });
      setName('');
      setDescription('');
      setCreating(false);
      load();
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Could not create group') });
    }
  }

  async function remove(g: Group) {
    if (!confirm(`Delete group “${g.name}”?`)) return;
    await deleteGroup(g.id).catch((e) => setToast({ ok: false, text: apiErrorMessage(e) }));
    load();
  }

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="row-between mb-16">
        <div>
          <h2 className="page-title" style={{ fontSize: 19 }}>Groups</h2>
          <p className="muted text-sm">Manage user groups used in permission grants.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ Create group</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <input className="input mb-16" placeholder="Search groups…" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 280 }} />

      {loading ? (
        <SpinnerCenter />
      ) : items.length === 0 ? (
        <EmptyState icon="👥" title="No groups" message="Create a group to organize users." />
      ) : (
        items.map((g) => {
          const readOnly = g.is_system || !!g.directory_source;
          return (
            <div className="list-row" key={g.id}>
              <div className="list-row-main">
                <div className="row gap-8">
                  <span className="list-row-title">{g.name}</span>
                  {g.is_system && <span className="badge">system</span>}
                  {g.directory_source && <span className="badge badge-info">{g.directory_source}</span>}
                </div>
                {g.description && <div className="muted text-xs">{g.description}</div>}
              </div>
              <div className="row gap-8">
                <button className="btn btn-sm" onClick={() => setDetailId(g.id)}>{readOnly ? 'View' : 'Members'}</button>
                {!readOnly && <button className="btn btn-ghost btn-sm" onClick={() => remove(g)}>×</button>}
              </div>
            </div>
          );
        })
      )}

      {creating && (
        <Modal
          open
          title="Create group"
          onClose={() => setCreating(false)}
          footer={
            <>
              <button className="btn" onClick={() => setCreating(false)}>Cancel</button>
              <button className="btn btn-primary" onClick={create} disabled={!name.trim()}>Create</button>
            </>
          }
        >
          <div className="field"><label>Name</label><input className="input" value={name} autoFocus onChange={(e) => setName(e.target.value)} /></div>
          <div className="field"><label>Description</label><input className="input" value={description} onChange={(e) => setDescription(e.target.value)} /></div>
        </Modal>
      )}

      {detailId && <GroupDetailModal groupId={detailId} onClose={() => setDetailId(null)} onError={(t) => setToast({ ok: false, text: t })} />}

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

function GroupDetailModal({ groupId, onClose, onError }: { groupId: string; onClose: () => void; onError: (t: string) => void }) {
  const [group, setGroup] = useState<GroupDetail | null>(null);
  const [picked, setPicked] = useState<User | null>(null);

  function load() {
    getGroup(groupId).then(setGroup).catch((e) => onError(apiErrorMessage(e)));
  }
  useEffect(load, [groupId]);

  const readOnly = !!group && (group.is_system || !!group.directory_source);

  async function add() {
    if (!picked) return;
    try {
      await addGroupMember(groupId, picked.id);
      setPicked(null);
      load();
    } catch (e) {
      onError(apiErrorMessage(e, 'Could not add member'));
    }
  }

  return (
    <Modal open title={group ? group.name : 'Group'} onClose={onClose} size="lg">
      {!group ? (
        <SpinnerCenter />
      ) : (
        <>
          {readOnly && (
            <div className="alert alert-info">
              {group.is_system ? 'System group — membership is managed automatically.' : `Directory group (${group.directory_source}) — synced and read-only.`}
            </div>
          )}
          {!readOnly && (
            <div className="row gap-8 mb-16" style={{ alignItems: 'flex-end' }}>
              <div className="flex-1" style={{ minWidth: 200 }}>
                <UserPicker value={picked} onChange={setPicked} allowUnassigned={false} placeholder="Add a person…" />
              </div>
              <button className="btn btn-primary" onClick={add} disabled={!picked}>Add</button>
            </div>
          )}
          <table className="data-table">
            <thead><tr><th>Member</th>{!readOnly && <th style={{ width: 80 }}></th>}</tr></thead>
            <tbody>
              {group.members.map((m) => (
                <tr key={m.id}>
                  <td>
                    <span className="row gap-8">
                      <Avatar name={m.display_name} size={26} /> {m.display_name}
                      <span className="text-xs muted">{m.email}</span>
                    </span>
                  </td>
                  {!readOnly && (
                    <td>
                      <button className="btn btn-ghost btn-sm" onClick={async () => { await removeGroupMember(groupId, m.id).catch(() => {}); load(); }}>Remove</button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {group.members.length === 0 && <div className="muted text-sm mt-8">No members.</div>}
        </>
      )}
    </Modal>
  );
}
