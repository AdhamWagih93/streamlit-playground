import { useEffect, useRef, useState } from 'react';
import {
  IssueDetail,
  Status,
  Priority,
  IssueType,
  Comment,
  Worklog,
  HistoryEntry,
  Attachment,
} from '../types';
import {
  getIssue,
  updateIssue,
  listComments,
  addComment,
  deleteComment,
  listWorklogs,
  addWorklog,
  deleteWorklog,
  getHistory,
  addLink,
  deleteLink,
  uploadAttachment,
  deleteAttachment,
  attachmentDownloadUrl,
} from '../api/issues';
import { getStatuses, getPriorities, getIssueTypes } from '../api/meta';
import { Avatar } from './Avatar';
import { UserPicker } from './UserPicker';
import { LabelInput } from './LabelInput';
import { IssueTypeIcon } from './IssueTypeIcon';
import { StatusBadge } from './StatusBadge';
import { Spinner, SpinnerCenter } from './Spinner';
import { useAuth } from '../store/auth';
import { timeAgo, formatDateTime, formatBytes } from '../lib/format';
import { apiErrorMessage } from '../api/client';

interface Props {
  issueKey: string;
  onChanged?: () => void;
}

type Tab = 'comments' | 'history' | 'worklog' | 'links';

export function IssueDetailView({ issueKey, onChanged }: Props) {
  const me = useAuth((s) => s.user);
  const [issue, setIssue] = useState<IssueDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const [statuses, setStatuses] = useState<Status[]>([]);
  const [priorities, setPriorities] = useState<Priority[]>([]);
  const [types, setTypes] = useState<IssueType[]>([]);

  const [summaryDraft, setSummaryDraft] = useState('');
  const [descDraft, setDescDraft] = useState('');
  const [editingDesc, setEditingDesc] = useState(false);

  const [tab, setTab] = useState<Tab>('comments');
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState('');
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [worklogs, setWorklogs] = useState<Worklog[]>([]);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const fileRef = useRef<HTMLInputElement>(null);

  async function reloadIssue() {
    const data = await getIssue(issueKey);
    setIssue(data);
    setSummaryDraft(data.summary);
    setDescDraft(data.description || '');
    setComments(data.comments || []);
    setWorklogs(data.worklogs || []);
    setAttachments(data.attachments || []);
  }

  useEffect(() => {
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const data = await getIssue(issueKey);
        if (!alive) return;
        setIssue(data);
        setSummaryDraft(data.summary);
        setDescDraft(data.description || '');
        setComments(data.comments || []);
        setWorklogs(data.worklogs || []);
        setAttachments(data.attachments || []);
        const [st, pr, ty] = await Promise.all([
          getStatuses(data.project.id),
          getPriorities(),
          getIssueTypes(data.project.id),
        ]);
        if (!alive) return;
        setStatuses(st);
        setPriorities(pr);
        setTypes(ty);
      } catch (e) {
        if (alive) setError(apiErrorMessage(e, 'Failed to load issue'));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [issueKey]);

  async function patch(payload: Parameters<typeof updateIssue>[1]) {
    if (!issue) return;
    try {
      const updated = await updateIssue(issue.key, payload);
      setIssue(updated);
      onChanged?.();
    } catch (e) {
      setError(apiErrorMessage(e, 'Update failed'));
    }
  }

  async function saveSummary() {
    if (!issue || summaryDraft.trim() === issue.summary || !summaryDraft.trim()) return;
    await patch({ summary: summaryDraft.trim() });
  }

  async function saveDesc() {
    setEditingDesc(false);
    if (!issue || descDraft === (issue.description || '')) return;
    await patch({ description: descDraft });
  }

  // Lazy-load tab content.
  useEffect(() => {
    if (!issue) return;
    if (tab === 'history') getHistory(issue.key).then(setHistory).catch(() => {});
    if (tab === 'worklog') listWorklogs(issue.key).then(setWorklogs).catch(() => {});
    if (tab === 'comments') listComments(issue.key).then(setComments).catch(() => {});
  }, [tab, issue?.key]);

  async function submitComment() {
    if (!issue || !newComment.trim()) return;
    try {
      const c = await addComment(issue.key, newComment.trim());
      setComments((cur) => [...cur, c]);
      setNewComment('');
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not add comment'));
    }
  }

  async function removeComment(id: string) {
    if (!issue) return;
    await deleteComment(issue.key, id).catch(() => {});
    setComments((cur) => cur.filter((c) => c.id !== id));
  }

  if (loading) return <SpinnerCenter />;
  if (!issue) return <div className="alert alert-error">{error || 'Issue not found'}</div>;

  return (
    <div>
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row gap-8 mb-8 text-sm muted">
        <IssueTypeIcon type={issue.type} />
        <span>{issue.project.name}</span>
        <span>/</span>
        <strong>{issue.key}</strong>
      </div>

      <input
        className="detail-summary-input"
        value={summaryDraft}
        onChange={(e) => setSummaryDraft(e.target.value)}
        onBlur={saveSummary}
        onKeyDown={(e) => {
          if (e.key === 'Enter') (e.target as HTMLInputElement).blur();
        }}
      />

      <div className="detail-grid">
        <div className="detail-main">
          <div className="detail-section">
            <h4>Description</h4>
            {editingDesc ? (
              <div>
                <textarea className="textarea" value={descDraft} onChange={(e) => setDescDraft(e.target.value)} autoFocus />
                <div className="row gap-8 mt-8">
                  <button className="btn btn-primary btn-sm" onClick={saveDesc}>
                    Save
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => {
                      setEditingDesc(false);
                      setDescDraft(issue.description || '');
                    }}
                  >
                    Cancel
                  </button>
                </div>
              </div>
            ) : (
              <div
                className="pointer"
                style={{ whiteSpace: 'pre-wrap', minHeight: 40, color: issue.description ? 'var(--text)' : 'var(--text-muted)' }}
                onClick={() => setEditingDesc(true)}
              >
                {issue.description || 'Add a description…'}
              </div>
            )}
          </div>

          {issue.subtasks && issue.subtasks.length > 0 && (
            <div className="detail-section">
              <h4>Subtasks</h4>
              {issue.subtasks.map((s) => (
                <div key={s.id} className="link-row">
                  <IssueTypeIcon type={s.type} />
                  <span className="text-xs muted">{s.key}</span>
                  <span className="flex-1">{s.summary}</span>
                  {s.status && <StatusBadge status={s.status} />}
                </div>
              ))}
            </div>
          )}

          <div className="detail-section">
            <h4>Attachments</h4>
            <input
              ref={fileRef}
              type="file"
              className="hidden"
              onChange={async (e) => {
                const f = e.target.files?.[0];
                if (!f) return;
                try {
                  const a = await uploadAttachment(issue.key, f);
                  setAttachments((cur) => [...cur, a]);
                } catch (err) {
                  setError(apiErrorMessage(err, 'Upload failed'));
                }
                if (fileRef.current) fileRef.current.value = '';
              }}
            />
            <button className="btn btn-sm" onClick={() => fileRef.current?.click()}>
              📎 Attach file
            </button>
            <div className="mt-8">
              {attachments.map((a) => (
                <div key={a.id} className="attachment-row">
                  <span className="flex-1">
                    <a href={attachmentDownloadUrl(a.id)} target="_blank" rel="noreferrer">
                      {a.filename}
                    </a>{' '}
                    <span className="text-xs muted">{formatBytes(a.size)}</span>
                  </span>
                  <button
                    className="btn btn-ghost btn-sm"
                    onClick={async () => {
                      await deleteAttachment(a.id).catch(() => {});
                      setAttachments((cur) => cur.filter((x) => x.id !== a.id));
                    }}
                  >
                    ×
                  </button>
                </div>
              ))}
            </div>
          </div>

          <div className="detail-section">
            <div className="tabs">
              <button className={`tab ${tab === 'comments' ? 'active' : ''}`} onClick={() => setTab('comments')}>
                Comments
              </button>
              <button className={`tab ${tab === 'history' ? 'active' : ''}`} onClick={() => setTab('history')}>
                History
              </button>
              <button className={`tab ${tab === 'worklog' ? 'active' : ''}`} onClick={() => setTab('worklog')}>
                Work log
              </button>
              <button className={`tab ${tab === 'links' ? 'active' : ''}`} onClick={() => setTab('links')}>
                Links
              </button>
            </div>

            {tab === 'comments' && (
              <div>
                {comments.map((c) => (
                  <div key={c.id} className="comment">
                    <Avatar user={c.author} size={32} />
                    <div className="comment-body-box">
                      <div className="comment-meta">
                        <span className="comment-author">{c.author.display_name}</span> · {timeAgo(c.created_at)}
                        {me?.id === c.author.id && (
                          <button className="btn btn-ghost btn-sm" style={{ float: 'right' }} onClick={() => removeComment(c.id)}>
                            Delete
                          </button>
                        )}
                      </div>
                      <div style={{ whiteSpace: 'pre-wrap' }}>{c.body}</div>
                    </div>
                  </div>
                ))}
                <div className="comment">
                  <Avatar user={me} size={32} />
                  <div className="flex-1">
                    <textarea
                      className="textarea"
                      value={newComment}
                      onChange={(e) => setNewComment(e.target.value)}
                      placeholder="Add a comment…"
                    />
                    <div className="mt-8">
                      <button className="btn btn-primary btn-sm" onClick={submitComment} disabled={!newComment.trim()}>
                        Comment
                      </button>
                    </div>
                  </div>
                </div>
              </div>
            )}

            {tab === 'history' && (
              <div>
                {history.length === 0 && <div className="muted text-sm">No history yet.</div>}
                {history.map((h) => (
                  <div key={h.id} className="timeline-item">
                    <Avatar user={h.author} size={24} />
                    <div className="flex-1">
                      <span className="comment-author">{h.author?.display_name || 'System'}</span>{' '}
                      changed <span className="field-name">{h.field}</span>{' '}
                      {h.old_value && <span className="muted">from “{h.old_value}” </span>}
                      to <strong>{h.new_value || '—'}</strong>
                      <div className="text-xs muted">{formatDateTime(h.created_at)}</div>
                    </div>
                  </div>
                ))}
              </div>
            )}

            {tab === 'worklog' && <WorklogTab issueKey={issue.key} worklogs={worklogs} setWorklogs={setWorklogs} />}

            {tab === 'links' && (
              <LinksTab
                issue={issue}
                onChanged={async () => {
                  await reloadIssue();
                }}
              />
            )}
          </div>
        </div>

        {/* ---------- Sidebar ---------- */}
        <div className="detail-side">
          <div className="side-box">
            <div className="side-row">
              <span className="label">Status</span>
              <select
                className="select"
                value={issue.status?.id || ''}
                onChange={(e) => patch({ status_id: e.target.value })}
              >
                {statuses.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="side-row">
              <span className="label">Assignee</span>
              <UserPicker value={issue.assignee || null} onChange={(u) => patch({ assignee_id: u?.id ?? null })} />
            </div>
            <div className="side-row">
              <span className="label">Reporter</span>
              <span className="row gap-8">
                {issue.reporter ? <Avatar user={issue.reporter} size={22} /> : null}
                {issue.reporter?.display_name || '—'}
              </span>
            </div>
            <div className="side-row">
              <span className="label">Priority</span>
              <select className="select" value={issue.priority?.id || ''} onChange={(e) => patch({ priority_id: e.target.value })}>
                <option value="">None</option>
                {priorities.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="side-row">
              <span className="label">Type</span>
              <select className="select" value={issue.type?.id || ''} onChange={(e) => patch({ type_id: e.target.value })}>
                {types.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="side-row">
              <span className="label">Story points</span>
              <input
                className="input"
                type="number"
                min="0"
                defaultValue={issue.story_points ?? ''}
                onBlur={(e) => {
                  const v = e.target.value;
                  patch({ story_points: v === '' ? null : Number(v) });
                }}
              />
            </div>
            <div className="side-row">
              <span className="label">Due date</span>
              <input
                className="input"
                type="date"
                defaultValue={issue.due_date ? issue.due_date.slice(0, 10) : ''}
                onChange={(e) => patch({ due_date: e.target.value || null })}
              />
            </div>
            <div className="side-row" style={{ alignItems: 'flex-start' }}>
              <span className="label">Labels</span>
              <LabelInput value={issue.labels || []} onChange={(labels) => patch({ label_names: labels })} />
            </div>
          </div>

          <div className="text-xs muted">
            Created {timeAgo(issue.created_at)} · Updated {timeAgo(issue.updated_at)}
          </div>
        </div>
      </div>
    </div>
  );
}

function WorklogTab({
  issueKey,
  worklogs,
  setWorklogs,
}: {
  issueKey: string;
  worklogs: Worklog[];
  setWorklogs: React.Dispatch<React.SetStateAction<Worklog[]>>;
}) {
  const [time, setTime] = useState('');
  const [comment, setComment] = useState('');
  const [busy, setBusy] = useState(false);

  async function submit() {
    if (!time.trim()) return;
    setBusy(true);
    try {
      const w = await addWorklog(issueKey, { time_spent: time.trim(), comment: comment || undefined });
      setWorklogs((cur) => [...cur, w]);
      setTime('');
      setComment('');
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {worklogs.map((w) => (
        <div key={w.id} className="worklog-row">
          <Avatar user={w.author} size={24} />
          <strong>{w.time_spent}</strong>
          <span className="flex-1 muted">{w.comment}</span>
          <span className="text-xs muted">{timeAgo(w.started_at || w.created_at)}</span>
          <button
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              await deleteWorklog(issueKey, w.id).catch(() => {});
              setWorklogs((cur) => cur.filter((x) => x.id !== w.id));
            }}
          >
            ×
          </button>
        </div>
      ))}
      <div className="row gap-8 mt-8 wrap">
        <input className="input" style={{ width: 120 }} placeholder="2h 30m" value={time} onChange={(e) => setTime(e.target.value)} />
        <input className="input flex-1" placeholder="What did you work on?" value={comment} onChange={(e) => setComment(e.target.value)} />
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !time.trim()}>
          Log
        </button>
      </div>
    </div>
  );
}

function LinksTab({ issue, onChanged }: { issue: IssueDetail; onChanged: () => void }) {
  const [linkType, setLinkType] = useState('relates to');
  const [targetKey, setTargetKey] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const LINK_TYPES = ['relates to', 'blocks', 'is blocked by', 'duplicates', 'is duplicated by'];

  async function submit() {
    if (!targetKey.trim()) return;
    setBusy(true);
    setError('');
    try {
      await addLink(issue.key, { link_type: linkType, target_key: targetKey.trim().toUpperCase() });
      setTargetKey('');
      onChanged();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not add link'));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      {error && <div className="alert alert-error">{error}</div>}
      {(issue.links || []).map((l) => (
        <div key={l.id} className="link-row">
          <span className="text-xs muted" style={{ width: 100 }}>
            {l.link_type}
          </span>
          <strong>{l.target_key}</strong>
          <span className="flex-1 muted">{l.target_summary}</span>
          {l.target_status && <StatusBadge status={l.target_status} />}
          <button
            className="btn btn-ghost btn-sm"
            onClick={async () => {
              await deleteLink(issue.key, l.id).catch(() => {});
              onChanged();
            }}
          >
            ×
          </button>
        </div>
      ))}
      <div className="row gap-8 mt-8 wrap">
        <select className="select" style={{ width: 160 }} value={linkType} onChange={(e) => setLinkType(e.target.value)}>
          {LINK_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          className="input flex-1"
          placeholder="ENG-123"
          value={targetKey}
          onChange={(e) => setTargetKey(e.target.value)}
          style={{ minWidth: 120 }}
        />
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !targetKey.trim()}>
          {busy ? <Spinner /> : 'Link'}
        </button>
      </div>
    </div>
  );
}
