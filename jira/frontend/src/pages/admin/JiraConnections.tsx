import { useEffect, useState } from 'react';
import { JiraConnection, JiraConnectionPayload, JiraRemoteProject, JiraAuthMode } from '../../types';
import {
  listJiraConnections,
  createJiraConnection,
  updateJiraConnection,
  deleteJiraConnection,
  testJiraConnection,
  listJiraConnectionProjects,
} from '../../api/admin';
import { Modal } from '../../components/Modal';
import { SpinnerCenter } from '../../components/Spinner';
import { EmptyState } from '../../components/EmptyState';
import { Toast, ToastMsg } from '../../components/Toast';
import { formatDate } from '../../lib/format';
import { apiErrorMessage } from '../../api/client';

export function JiraConnections() {
  const [items, setItems] = useState<JiraConnection[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [toast, setToast] = useState<ToastMsg | null>(null);
  const [editing, setEditing] = useState<JiraConnection | null>(null);
  const [creating, setCreating] = useState(false);
  const [testingId, setTestingId] = useState<string | null>(null);
  const [browsing, setBrowsing] = useState<JiraConnection | null>(null);

  function load() {
    setLoading(true);
    listJiraConnections()
      .then(setItems)
      .catch((e) => setError(apiErrorMessage(e)))
      .finally(() => setLoading(false));
  }
  useEffect(load, []);

  async function runTest(c: JiraConnection) {
    setTestingId(c.id);
    try {
      const res = await testJiraConnection(c.id);
      setToast({ ok: res.ok, text: res.account ? `${res.message} (${res.account})` : res.message });
      load();
    } catch (e) {
      setToast({ ok: false, text: apiErrorMessage(e, 'Test failed') });
    } finally {
      setTestingId(null);
    }
  }

  async function remove(c: JiraConnection) {
    if (!confirm(`Delete connection “${c.name}”?`)) return;
    await deleteJiraConnection(c.id).catch((e) => setToast({ ok: false, text: apiErrorMessage(e) }));
    load();
  }

  if (loading) return <SpinnerCenter />;

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="row-between mb-16">
        <div>
          <h2 className="page-title" style={{ fontSize: 19 }}>Jira Connections</h2>
          <p className="muted text-sm">Connect external Jira instances for import and sync.</p>
        </div>
        <button className="btn btn-primary" onClick={() => setCreating(true)}>+ Add connection</button>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      {items.length === 0 ? (
        <EmptyState icon="🔌" title="No connections yet" message="Add a Jira connection to start importing projects." />
      ) : (
        items.map((c) => (
          <div className="list-row" key={c.id}>
            <div className="list-row-main">
              <div className="row gap-8">
                <span className="list-row-title">{c.name}</span>
                {c.is_default && <span className="badge badge-info">default</span>}
                {!c.enabled && <span className="badge">disabled</span>}
                {c.last_check_ok === true && <span className="badge badge-ok">OK</span>}
                {c.last_check_ok === false && <span className="badge badge-err">Failed</span>}
              </div>
              <div className="muted text-xs">
                {c.base_url} · {c.auth_mode}
                {c.last_checked_at && <> · checked {formatDate(c.last_checked_at)}</>}
              </div>
            </div>
            <div className="row gap-8">
              <button className="btn btn-sm" onClick={() => runTest(c)} disabled={testingId === c.id}>
                {testingId === c.id ? 'Testing…' : 'Test'}
              </button>
              <button className="btn btn-sm" onClick={() => setBrowsing(c)}>Browse projects</button>
              <button className="btn btn-sm" onClick={() => setEditing(c)}>Edit</button>
              <button className="btn btn-ghost btn-sm" onClick={() => remove(c)}>×</button>
            </div>
          </div>
        ))
      )}

      {(creating || editing) && (
        <ConnectionModal
          connection={editing}
          onClose={() => { setCreating(false); setEditing(null); }}
          onSaved={() => { setCreating(false); setEditing(null); load(); }}
          onError={(t) => setToast({ ok: false, text: t })}
        />
      )}

      {browsing && <BrowseProjectsModal connection={browsing} onClose={() => setBrowsing(null)} />}

      <Toast toast={toast} onClose={() => setToast(null)} />
    </div>
  );
}

function ConnectionModal({
  connection,
  onClose,
  onSaved,
  onError,
}: {
  connection: JiraConnection | null;
  onClose: () => void;
  onSaved: () => void;
  onError: (t: string) => void;
}) {
  const [name, setName] = useState(connection?.name || '');
  const [baseUrl, setBaseUrl] = useState(connection?.base_url || '');
  const [authMode, setAuthMode] = useState<JiraAuthMode>(connection?.auth_mode || 'cloud');
  const [email, setEmail] = useState(connection?.email || '');
  const [apiToken, setApiToken] = useState('');
  const [verifySsl, setVerifySsl] = useState(connection?.verify_ssl ?? true);
  const [enabled, setEnabled] = useState(connection?.enabled ?? true);
  const [isDefault, setIsDefault] = useState(connection?.is_default ?? false);
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    const payload: JiraConnectionPayload = {
      name,
      base_url: baseUrl,
      auth_mode: authMode,
      email,
      verify_ssl: verifySsl,
      enabled,
      is_default: isDefault,
    };
    if (apiToken) payload.api_token = apiToken;
    try {
      if (connection) await updateJiraConnection(connection.id, payload);
      else await createJiraConnection(payload);
      onSaved();
    } catch (e) {
      onError(apiErrorMessage(e, 'Could not save connection'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      open
      title={connection ? 'Edit connection' : 'Add Jira connection'}
      onClose={onClose}
      footer={
        <>
          <button className="btn" onClick={onClose}>Cancel</button>
          <button className="btn btn-primary" onClick={save} disabled={busy || !name || !baseUrl}>
            {busy ? 'Saving…' : 'Save'}
          </button>
        </>
      }
    >
      <div className="field">
        <label>Name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} />
      </div>
      <div className="field">
        <label>Base URL</label>
        <input className="input" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://your.atlassian.net" />
      </div>
      <div className="field-grid">
        <div className="field">
          <label>Auth mode</label>
          <select className="select" value={authMode} onChange={(e) => setAuthMode(e.target.value as JiraAuthMode)}>
            <option value="cloud">Cloud</option>
            <option value="server">Server / DC</option>
          </select>
        </div>
        <div className="field">
          <label>Email</label>
          <input className="input" value={email} onChange={(e) => setEmail(e.target.value)} />
        </div>
      </div>
      <div className="field">
        <label>API token {connection?.token_set && <span className="badge badge-info">token set</span>}</label>
        <input
          className="input"
          type="password"
          value={apiToken}
          onChange={(e) => setApiToken(e.target.value)}
          placeholder={connection?.token_set ? 'Leave blank to keep' : ''}
        />
      </div>
      <div className="row gap-16 wrap">
        <label className="row gap-8"><input type="checkbox" checked={verifySsl} onChange={(e) => setVerifySsl(e.target.checked)} /> Verify SSL</label>
        <label className="row gap-8"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /> Enabled</label>
        <label className="row gap-8"><input type="checkbox" checked={isDefault} onChange={(e) => setIsDefault(e.target.checked)} /> Default</label>
      </div>
    </Modal>
  );
}

function BrowseProjectsModal({ connection, onClose }: { connection: JiraConnection; onClose: () => void }) {
  const [projects, setProjects] = useState<JiraRemoteProject[] | null>(null);
  const [error, setError] = useState('');

  useEffect(() => {
    listJiraConnectionProjects(connection.id)
      .then(setProjects)
      .catch((e) => setError(apiErrorMessage(e)));
  }, [connection.id]);

  return (
    <Modal open title={`Projects on ${connection.name}`} onClose={onClose} size="lg">
      {error && <div className="alert alert-error">{error}</div>}
      {!projects && !error && <SpinnerCenter />}
      {projects && projects.length === 0 && <div className="muted">No projects found.</div>}
      {projects && projects.length > 0 && (
        <table className="data-table">
          <thead>
            <tr><th style={{ width: 90 }}>Key</th><th>Name</th><th>Lead</th><th style={{ width: 110 }}></th></tr>
          </thead>
          <tbody>
            {projects.map((p) => (
              <tr key={p.id}>
                <td><strong>{p.key}</strong></td>
                <td>{p.name}</td>
                <td className="muted">{p.lead}</td>
                <td>{p.exists_locally && <span className="badge badge-ok">local</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Modal>
  );
}
