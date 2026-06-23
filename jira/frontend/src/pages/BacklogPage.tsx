import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import {
  DndContext,
  DragEndEvent,
  DragStartEvent,
  DragOverlay,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
} from '@dnd-kit/core';
import { BacklogData, Board, IssueListItem, Sprint } from '../types';
import { listBoards, getBacklog, createSprint, startSprint, completeSprint, deleteSprint } from '../api/agile';
import { getProject } from '../api/projects';
import { rankIssue, createIssue } from '../api/issues';
import { getIssueTypes } from '../api/meta';
import { IssueTypeIcon } from '../components/IssueTypeIcon';
import { PriorityIcon } from '../components/PriorityIcon';
import { Avatar } from '../components/Avatar';
import { StatusBadge } from '../components/StatusBadge';
import { SpinnerCenter } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { IssueDetailModal } from '../components/IssueDetailModal';
import { apiErrorMessage } from '../api/client';

const BACKLOG = 'backlog';

function Row({ issue, onOpen }: { issue: IssueListItem; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: issue.id, data: { issue } });
  return (
    <div ref={setNodeRef} className={`backlog-row ${isDragging ? 'dragging' : ''}`} {...attributes} {...listeners} onClick={onOpen}>
      <IssueTypeIcon type={issue.type} />
      <span className="text-xs muted nowrap">{issue.key}</span>
      <span className="summary">{issue.summary}</span>
      <PriorityIcon priority={issue.priority} />
      {issue.story_points != null && <span className="story-points">{issue.story_points}</span>}
      {issue.status && <StatusBadge status={issue.status} />}
      {issue.assignee && <Avatar user={issue.assignee} size={22} />}
    </div>
  );
}

function DropZone({
  id,
  title,
  sprint,
  issues,
  onOpen,
  board,
  reload,
  setError,
}: {
  id: string;
  title: React.ReactNode;
  sprint?: Sprint;
  issues: IssueListItem[];
  onOpen: (i: IssueListItem) => void;
  board: Board;
  reload: () => void;
  setError: (s: string) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id, data: { sprintId: sprint ? sprint.id : null } });
  const [adding, setAdding] = useState(false);
  const [summary, setSummary] = useState('');

  async function quickAdd() {
    if (!summary.trim()) {
      setAdding(false);
      return;
    }
    try {
      const types = await getIssueTypes(board.project_id);
      const type = types.find((t) => !t.is_subtask) || types[0];
      if (!type) throw new Error('No issue type configured');
      await createIssue({
        project_id: board.project_id,
        type_id: type.id,
        summary: summary.trim(),
        sprint_id: sprint ? sprint.id : null,
      });
      setSummary('');
      setAdding(false);
      reload();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create issue'));
    }
  }

  return (
    <div className="backlog-section">
      <div className="backlog-section-head">
        <div className="backlog-section-title">{title}</div>
        <div className="row gap-8">
          <span className="muted text-sm">{issues.length} issues</span>
          {sprint && sprint.state === 'future' && (
            <button className="btn btn-primary btn-sm" onClick={() => startSprint(sprint.id).then(reload).catch((e) => setError(apiErrorMessage(e)))}>
              Start sprint
            </button>
          )}
          {sprint && sprint.state === 'active' && (
            <button className="btn btn-sm" onClick={() => completeSprint(sprint.id).then(reload).catch((e) => setError(apiErrorMessage(e)))}>
              Complete sprint
            </button>
          )}
          {sprint && sprint.state === 'future' && (
            <button
              className="btn btn-ghost btn-sm"
              onClick={() => {
                if (confirm('Delete this sprint?')) deleteSprint(sprint.id).then(reload).catch((e) => setError(apiErrorMessage(e)));
              }}
            >
              ×
            </button>
          )}
        </div>
      </div>
      <div ref={setNodeRef} className={`backlog-section-body ${isOver ? 'drag-over' : ''}`}>
        {issues.length === 0 && <div className="backlog-empty">Drag issues here</div>}
        {issues.map((i) => (
          <Row key={i.id} issue={i} onOpen={() => onOpen(i)} />
        ))}
        {adding ? (
          <div className="row gap-8" style={{ padding: '6px 10px' }}>
            <input
              className="input"
              autoFocus
              value={summary}
              placeholder="What needs to be done?"
              onChange={(e) => setSummary(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') quickAdd();
                if (e.key === 'Escape') {
                  setAdding(false);
                  setSummary('');
                }
              }}
              onBlur={quickAdd}
            />
          </div>
        ) : (
          <button className="btn btn-ghost btn-sm" onClick={() => setAdding(true)} style={{ margin: '4px 6px' }}>
            + Create issue
          </button>
        )}
      </div>
    </div>
  );
}

export function BacklogPage() {
  const { projectKey } = useParams();
  const [board, setBoard] = useState<Board | null>(null);
  const [data, setData] = useState<BacklogData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeIssue, setActiveIssue] = useState<IssueListItem | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);

  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const reload = useCallback(async (boardId: string) => {
    const d = await getBacklog(boardId);
    setData(d);
  }, []);

  useEffect(() => {
    if (!projectKey) return;
    let alive = true;
    setLoading(true);
    (async () => {
      try {
        const proj = await getProject(projectKey);
        const boards = await listBoards(proj.id);
        if (!alive) return;
        if (boards.length === 0) {
          setBoard(null);
          setLoading(false);
          return;
        }
        setBoard(boards[0]);
        await reload(boards[0].id);
      } catch (e) {
        if (alive) setError(apiErrorMessage(e, 'Failed to load backlog'));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [projectKey, reload]);

  function onDragStart(e: DragStartEvent) {
    setActiveIssue((e.active.data.current?.issue as IssueListItem) || null);
  }

  async function onDragEnd(e: DragEndEvent) {
    setActiveIssue(null);
    if (!board || !data) return;
    const { active, over } = e;
    if (!over) return;
    const issue = active.data.current?.issue as IssueListItem | undefined;
    if (!issue) return;
    const targetSprintId = (over.data.current?.sprintId as string | null | undefined) ?? null;
    if ((issue.sprint_id ?? null) === targetSprintId) return;

    const prev = data;
    // Optimistic: move issue between buckets.
    const next: BacklogData = {
      ...data,
      sprint_issues: { ...data.sprint_issues },
      backlog: data.backlog.filter((i) => i.id !== issue.id),
    };
    Object.keys(next.sprint_issues).forEach((sid) => {
      next.sprint_issues[sid] = next.sprint_issues[sid].filter((i) => i.id !== issue.id);
    });
    const moved = { ...issue, sprint_id: targetSprintId };
    if (targetSprintId) {
      next.sprint_issues[targetSprintId] = [...(next.sprint_issues[targetSprintId] || []), moved];
    } else {
      next.backlog = [...next.backlog, moved];
    }
    setData(next);

    try {
      await rankIssue(issue.key, { sprint_id: targetSprintId });
    } catch (err) {
      setData(prev);
      setError(apiErrorMessage(err, 'Could not move issue'));
    }
  }

  async function addSprint() {
    if (!board) return;
    const name = prompt('Sprint name', `Sprint ${(data?.sprints.length || 0) + 1}`);
    if (!name) return;
    try {
      await createSprint(board.id, { name });
      reload(board.id);
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create sprint'));
    }
  }

  if (loading) return <SpinnerCenter />;
  if (!board) {
    return (
      <div className="page">
        <EmptyState icon="📚" title="No board" message="This project has no agile board." />
      </div>
    );
  }

  return (
    <div className="page">
      <div className="breadcrumb">{projectKey} / Backlog</div>
      <div className="page-header">
        <h1 className="page-title">Backlog</h1>
        <button className="btn btn-primary" onClick={addSprint}>
          + Create sprint
        </button>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        {data?.sprints.map((s) => (
          <DropZone
            key={s.id}
            id={`sprint:${s.id}`}
            sprint={s}
            board={board}
            reload={() => reload(board.id)}
            setError={setError}
            onOpen={(i) => setOpenKey(i.key)}
            title={
              <span className="row gap-8">
                {s.name}
                <span className={`status-badge status-${s.state === 'active' ? 'in_progress' : s.state === 'closed' ? 'done' : 'todo'}`}>
                  {s.state}
                </span>
                {s.goal && <span className="muted text-sm">{s.goal}</span>}
              </span>
            }
            issues={data.sprint_issues[s.id] || []}
          />
        ))}
        <DropZone
          id={BACKLOG}
          board={board}
          reload={() => reload(board.id)}
          setError={setError}
          onOpen={(i) => setOpenKey(i.key)}
          title="Backlog"
          issues={data?.backlog || []}
        />

        <DragOverlay>
          {activeIssue ? (
            <div className="backlog-row" style={{ background: '#fff', boxShadow: 'var(--shadow)' }}>
              <IssueTypeIcon type={activeIssue.type} />
              <span className="summary">{activeIssue.summary}</span>
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {openKey && <IssueDetailModal issueKey={openKey} onClose={() => setOpenKey(null)} onChanged={() => board && reload(board.id)} />}
    </div>
  );
}
