import { useCallback, useEffect, useMemo, useState } from 'react';
import { useParams, useSearchParams } from 'react-router-dom';
import {
  DndContext,
  DragEndEvent,
  DragOverlay,
  DragStartEvent,
  PointerSensor,
  useSensor,
  useSensors,
  useDraggable,
  useDroppable,
} from '@dnd-kit/core';
import { BoardData, BoardColumn, IssueListItem, Board } from '../types';
import { listBoards, getBoard } from '../api/agile';
import { getProject } from '../api/projects';
import { rankIssue } from '../api/issues';
import { IssueCardContent } from '../components/IssueCard';
import { SpinnerCenter } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { useUI } from '../store/ui';
import { IssueDetailModal } from '../components/IssueDetailModal';
import { apiErrorMessage } from '../api/client';

function DraggableCard({ issue, onOpen }: { issue: IssueListItem; onOpen: () => void }) {
  const { attributes, listeners, setNodeRef, isDragging } = useDraggable({ id: issue.id, data: { issue } });
  return (
    <div
      ref={setNodeRef}
      className={`issue-card ${isDragging ? 'dragging' : ''}`}
      {...attributes}
      {...listeners}
      onClick={onOpen}
    >
      <IssueCardContent issue={issue} />
    </div>
  );
}

function Column({
  column,
  onOpen,
}: {
  column: BoardColumn;
  onOpen: (issue: IssueListItem) => void;
}) {
  const { setNodeRef, isOver } = useDroppable({ id: `col:${column.status_id}`, data: { statusId: column.status_id } });
  return (
    <div className="board-column">
      <div className="board-column-head">
        <span>{column.status_name}</span>
        <span className="board-column-count">{column.issues.length}</span>
      </div>
      <div ref={setNodeRef} className={`board-column-body ${isOver ? 'drag-over' : ''}`}>
        {column.issues.map((issue) => (
          <DraggableCard key={issue.id} issue={issue} onOpen={() => onOpen(issue)} />
        ))}
      </div>
    </div>
  );
}

export function BoardPage() {
  const { projectKey } = useParams();
  const [searchParams] = useSearchParams();
  const [board, setBoard] = useState<Board | null>(null);
  const [data, setData] = useState<BoardData | null>(null);
  const [columns, setColumns] = useState<BoardColumn[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [activeIssue, setActiveIssue] = useState<IssueListItem | null>(null);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const issueCreatedTick = useUI((s) => s.issueCreatedTick);

  const sprintParam = searchParams.get('sprint') || undefined;
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 5 } }));

  const loadBoard = useCallback(
    async (boardId: string, sprintId?: string) => {
      const d = await getBoard(boardId, sprintId);
      setData(d);
      setColumns(d.columns);
    },
    []
  );

  useEffect(() => {
    if (!projectKey) return;
    let alive = true;
    setLoading(true);
    setError('');
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
        const b = boards[0];
        setBoard(b);
        await loadBoard(b.id, sprintParam);
      } catch (e) {
        if (alive) setError(apiErrorMessage(e, 'Failed to load board'));
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, [projectKey, sprintParam, loadBoard, issueCreatedTick]);

  function onDragStart(e: DragStartEvent) {
    setActiveIssue((e.active.data.current?.issue as IssueListItem) || null);
  }

  async function onDragEnd(e: DragEndEvent) {
    setActiveIssue(null);
    const { active, over } = e;
    if (!over) return;
    const issue = active.data.current?.issue as IssueListItem | undefined;
    const targetStatusId = over.data.current?.statusId as string | undefined;
    if (!issue || !targetStatusId || issue.status?.id === targetStatusId) return;

    const prevColumns = columns;
    // Optimistic move to bottom of target column.
    const next = columns.map((c) => ({ ...c, issues: c.issues.filter((i) => i.id !== issue.id) }));
    const targetCol = next.find((c) => c.status_id === targetStatusId);
    const movedIssue: IssueListItem = {
      ...issue,
      status: targetCol ? { id: targetCol.status_id, name: targetCol.status_name, category: targetCol.category } : issue.status,
    };
    const lastId = targetCol?.issues[targetCol.issues.length - 1]?.id;
    targetCol?.issues.push(movedIssue);
    setColumns(next);

    try {
      await rankIssue(issue.key, { status_id: targetStatusId, after_id: lastId });
    } catch (err) {
      setColumns(prevColumns);
      setError(apiErrorMessage(err, 'Could not move issue'));
    }
  }

  const activeSprint = data?.active_sprint;
  const totalIssues = useMemo(() => columns.reduce((n, c) => n + c.issues.length, 0), [columns]);

  if (loading) return <SpinnerCenter />;

  if (!board) {
    return (
      <div className="page">
        <EmptyState icon="📋" title="No board for this project" message="This project has no agile board configured yet." />
      </div>
    );
  }

  return (
    <div className="page" style={{ maxWidth: '100%' }}>
      <div className="breadcrumb">
        {projectKey} / Board
      </div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{board.name}</h1>
          <div className="page-subtitle">{totalIssues} issues on board</div>
        </div>
      </div>

      {error && <div className="alert alert-error">{error}</div>}

      {activeSprint && (
        <div className="sprint-banner">
          <div>
            <strong>{activeSprint.name}</strong>
            {activeSprint.goal && <span className="muted"> — {activeSprint.goal}</span>}
          </div>
          <span className="status-badge status-in_progress">Active sprint</span>
        </div>
      )}

      <DndContext sensors={sensors} onDragStart={onDragStart} onDragEnd={onDragEnd}>
        <div className="board-columns">
          {columns.map((c) => (
            <Column key={c.status_id} column={c} onOpen={(i) => setOpenKey(i.key)} />
          ))}
        </div>
        <DragOverlay>
          {activeIssue ? (
            <div className="issue-card" style={{ width: 260 }}>
              <IssueCardContent issue={activeIssue} />
            </div>
          ) : null}
        </DragOverlay>
      </DndContext>

      {openKey && (
        <IssueDetailModal
          issueKey={openKey}
          onClose={() => setOpenKey(null)}
          onChanged={() => board && loadBoard(board.id, sprintParam)}
        />
      )}
    </div>
  );
}
