import { useEffect, useRef, useState } from 'react';
import { SyncLink, SyncDiscover } from '../../types';
import {
  getSync,
  discoverSync,
  linkSync,
  startSync,
  pauseSync,
  resumeSync,
  unlinkSync,
  listSyncConnections,
  type SyncConnection,
} from '../../api/sync';
import { SpinnerCenter } from '../../components/Spinner';
import { formatDateTime } from '../../lib/format';
import { apiErrorMessage } from '../../api/client';

interface Props {
  projectId: string;
  isAdmin: boolean;
  setError: (s: string) => void;
}

export function JiraSyncTab({ projectId, isAdmin, setError }: Props) {
  const [link, setLink] = useState<SyncLink | null>(null);
  const [loading, setLoading] = useState(true);
  const pollRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  function clearPoll() {
    if (pollRef.current) {
      clearTimeout(pollRef.current);
      pollRef.current = null;
    }
  }

  async function refresh() {
    try {
      const l = await getSync(projectId);
      setLink(l);
      return l;
    } catch (e) {
      setError(apiErrorMessage(e));
      return null;
    }
  }

  useEffect(() => {
    refresh().finally(() => setLoading(false));
    return clearPoll;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Poll every ~2s while running.
  useEffect(() => {
    clearPoll();
    if (link?.status === 'running') {
      pollRef.current = setTimeout(() => {
        refresh();
      }, 2000);
    }
    return clearPoll;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [link]);

  if (loading) return <SpinnerCenter />;

  if (!link) {
    return <LinkForm projectId={projectId} onLinked={(l) => setLink(l)} setError={setError} />;
  }

  return <LinkedView projectId={projectId} link={link} isAdmin={isAdmin} onChanged={(l) => setLink(l)} onUnlinked={() => setLink(null)} setError={setError} />;
}

function LinkForm({ projectId, onLinked, setError }: { projectId: string; onLinked: (l: SyncLink) => void; setError: (s: string) => void }) {
  const [connections, setConnections] = useState<SyncConnection[]>([]);
  const [connectionId, setConnectionId] = useState('');
  const [discovery, setDiscovery] = useState<SyncDiscover | null>(null);
  const [discovering, setDiscovering] = useState(false);
  const [syncPermissions, setSyncPermissions] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listSyncConnections()
      .then((c) => {
        setConnections(c);
        const def = c.find((x) => x.is_default) || c[0];
        if (def) setConnectionId(String(def.id));
      })
      .catch((e) => setError(apiErrorMessage(e)));
  }, []);

  async function discover() {
    setDiscovering(true);
    setDiscovery(null);
    try {
      const d = await discoverSync(projectId, connectionId || undefined);
      setDiscovery(d);
    } catch (e) {
      setError(apiErrorMessage(e, 'Discovery failed'));
    } finally {
      setDiscovering(false);
    }
  }

  async function link() {
    setBusy(true);
    try {
      const l = await linkSync(projectId, {
        connection_id: connectionId || undefined,
        jira_project_key: discovery?.jira_project_key || undefined,
        sync_permissions: syncPermissions,
      });
      onLinked(l);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not link project'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ maxWidth: 640 }}>
      <div className="section-card">
        <h3>Link to a Jira project</h3>
        <p className="muted text-sm mb-8">Match this project to a Jira project by its key to enable sync.</p>
        <div className="field">
          <label>Connection</label>
          <select className="select" value={connectionId} onChange={(e) => setConnectionId(e.target.value)}>
            <option value="">Select connection…</option>
            {connections.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
          </select>
        </div>
        <button className="btn" onClick={discover} disabled={discovering || !connectionId}>
          {discovering ? 'Discovering…' : 'Discover by project key'}
        </button>

        {discovery && (
          <div className={`alert mt-16 ${discovery.found ? 'alert-success' : 'alert-error'}`}>
            {discovery.found
              ? `Found: ${discovery.name} (${discovery.jira_project_key}) — ${discovery.issue_count ?? 0} issues`
              : discovery.message || 'No matching Jira project found.'}
          </div>
        )}

        {discovery?.found && (
          <>
            <label className="row gap-8 mt-16">
              <input type="checkbox" checked={syncPermissions} onChange={(e) => setSyncPermissions(e.target.checked)} />
              Also sync permissions
            </label>
            <button className="btn btn-primary mt-16" onClick={link} disabled={busy}>
              {busy ? 'Linking…' : 'Link project'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}

function LinkedView({
  projectId,
  link,
  isAdmin,
  onChanged,
  onUnlinked,
  setError,
}: {
  projectId: string;
  link: SyncLink;
  isAdmin: boolean;
  onChanged: (l: SyncLink) => void;
  onUnlinked: () => void;
  setError: (s: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const pct = link.total_issues > 0 ? Math.min(100, Math.round((link.processed_issues / link.total_issues) * 100)) : 0;

  async function action(fn: () => Promise<{ link: SyncLink }>) {
    setBusy(true);
    try {
      const res = await fn();
      onChanged(res.link);
    } catch (e) {
      setError(apiErrorMessage(e, 'Sync action failed'));
    } finally {
      setBusy(false);
    }
  }

  async function toggleSyncPermissions() {
    try {
      const l = await linkSync(projectId, {
        connection_id: link.connection_id,
        jira_project_key: link.jira_project_key,
        sync_permissions: !link.sync_permissions,
      });
      onChanged(l);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not update'));
    }
  }

  async function unlink() {
    if (!confirm('Unlink this project from Jira? Sync state will be removed.')) return;
    setBusy(true);
    try {
      await unlinkSync(projectId);
      onUnlinked();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not unlink'));
    } finally {
      setBusy(false);
    }
  }

  const statusBadge =
    link.status === 'running' ? 'badge-info'
    : link.status === 'error' ? 'badge-err'
    : link.status === 'completed' ? 'badge-ok'
    : link.status === 'paused' ? 'badge-warn'
    : 'badge';

  return (
    <div style={{ maxWidth: 760 }}>
      <div className="section-card">
        <div className="row-between">
          <h3>{link.jira_project_key}</h3>
          <span className={`badge ${statusBadge}`} style={{ textTransform: 'capitalize' }}>{link.status}</span>
        </div>
        <div className="muted text-xs mb-8">
          {link.last_synced_at && <>Last synced {formatDateTime(link.last_synced_at)} · </>}
          {link.processed_issues} / {link.total_issues} issues
        </div>

        <div className="progress-bar mb-16"><div className="progress-bar-fill" style={{ width: `${pct}%` }} /></div>

        <div className="row gap-8 wrap">
          {link.status !== 'running' && (
            <button className="btn btn-primary" onClick={() => action(() => startSync(projectId))} disabled={busy}>
              {link.status === 'paused' ? 'Resume' : 'Start sync'}
            </button>
          )}
          {link.status === 'running' && (
            <button className="btn" onClick={() => action(() => pauseSync(projectId))} disabled={busy}>Pause</button>
          )}
          {link.status === 'paused' && (
            <button className="btn" onClick={() => action(() => resumeSync(projectId))} disabled={busy}>Resume</button>
          )}
          {isAdmin && <button className="btn btn-danger" onClick={unlink} disabled={busy}>Unlink</button>}
        </div>

        {link.last_error && <div className="alert alert-error mt-16">{link.last_error}</div>}

        <label className="row gap-8 mt-16">
          <input type="checkbox" checked={link.sync_permissions} onChange={toggleSyncPermissions} />
          Sync permissions
        </label>
      </div>

      <h3 style={{ fontSize: 16, margin: '16px 0 8px' }}>Run history</h3>
      {link.recent_runs.length === 0 ? (
        <div className="muted text-sm">No runs yet.</div>
      ) : (
        <table className="data-table">
          <thead>
            <tr>
              <th>Started</th><th>Status</th><th>Trigger</th>
              <th style={{ width: 80 }}>Processed</th><th style={{ width: 70 }}>Created</th>
              <th style={{ width: 70 }}>Updated</th><th style={{ width: 60 }}>Errors</th>
            </tr>
          </thead>
          <tbody>
            {link.recent_runs.map((r) => (
              <tr key={r.id}>
                <td className="muted">{formatDateTime(r.started_at)}</td>
                <td style={{ textTransform: 'capitalize' }}>{r.status}</td>
                <td className="muted">{r.trigger}</td>
                <td>{r.processed}</td>
                <td>{r.created}</td>
                <td>{r.updated}</td>
                <td>{r.errors}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
