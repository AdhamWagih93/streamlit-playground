import { useEffect, useState } from 'react';
import { Modal } from './Modal';
import { UserPicker } from './UserPicker';
import { LabelInput } from './LabelInput';
import { ComponentInput } from './ComponentInput';
import { ProjectBrief, IssueType, Priority, User, IssueDetail } from '../types';
import { listProjects } from '../api/projects';
import { getIssueTypes, getPriorities } from '../api/meta';
import { createIssue } from '../api/issues';
import { apiErrorMessage } from '../api/client';

interface Props {
  open: boolean;
  onClose: () => void;
  defaultProjectId?: string;
  defaultSprintId?: string | null;
  defaultEpicId?: string;
  defaultParentId?: string;
  onCreated?: (issue: IssueDetail) => void;
}

export function CreateIssueModal({
  open,
  onClose,
  defaultProjectId,
  defaultSprintId,
  defaultEpicId,
  defaultParentId,
  onCreated,
}: Props) {
  const [projects, setProjects] = useState<ProjectBrief[]>([]);
  const [types, setTypes] = useState<IssueType[]>([]);
  const [priorities, setPriorities] = useState<Priority[]>([]);

  const [projectId, setProjectId] = useState(defaultProjectId || '');
  const [typeId, setTypeId] = useState('');
  const [summary, setSummary] = useState('');
  const [description, setDescription] = useState('');
  const [priorityId, setPriorityId] = useState('');
  const [assignee, setAssignee] = useState<User | null>(null);
  const [storyPoints, setStoryPoints] = useState('');
  const [labels, setLabels] = useState<string[]>([]);
  const [componentIds, setComponentIds] = useState<string[]>([]);

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!open) return;
    listProjects().then(setProjects).catch(() => {});
    getPriorities().then(setPriorities).catch(() => {});
  }, [open]);

  useEffect(() => {
    if (open) {
      setProjectId(defaultProjectId || '');
      setSummary('');
      setDescription('');
      setAssignee(null);
      setStoryPoints('');
      setLabels([]);
      setComponentIds([]);
      setError('');
    }
  }, [open, defaultProjectId]);

  useEffect(() => {
    // Components are project-scoped — clear any selection when the project changes.
    setComponentIds([]);
    if (!projectId) {
      setTypes([]);
      return;
    }
    getIssueTypes(projectId)
      .then((t) => {
        setTypes(t);
        setTypeId((cur) => cur || t.find((x) => !x.is_subtask)?.id || t[0]?.id || '');
      })
      .catch(() => {});
  }, [projectId]);

  async function submit() {
    setError('');
    if (!projectId) return setError('Pick a project');
    if (!typeId) return setError('Pick an issue type');
    if (!summary.trim()) return setError('Summary is required');
    setSaving(true);
    try {
      const issue = await createIssue({
        project_id: projectId,
        type_id: typeId,
        summary: summary.trim(),
        description: description || undefined,
        priority_id: priorityId || undefined,
        assignee_id: assignee?.id ?? undefined,
        sprint_id: defaultSprintId ?? undefined,
        epic_id: defaultEpicId || undefined,
        parent_id: defaultParentId || undefined,
        story_points: storyPoints ? Number(storyPoints) : undefined,
        label_names: labels,
        component_ids: componentIds.length ? componentIds : undefined,
      });
      onCreated?.(issue);
      onClose();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not create issue'));
    } finally {
      setSaving(false);
    }
  }

  return (
    <Modal
      open={open}
      onClose={onClose}
      title="Create issue"
      size="lg"
      footer={
        <>
          <button className="btn" onClick={onClose} disabled={saving}>
            Cancel
          </button>
          <button className="btn btn-primary" onClick={submit} disabled={saving}>
            {saving ? 'Creating…' : 'Create'}
          </button>
        </>
      }
    >
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row gap-16 wrap">
        <div className="field flex-1" style={{ minWidth: 200 }}>
          <label>Project</label>
          <select className="select" value={projectId} onChange={(e) => setProjectId(e.target.value)} disabled={!!defaultProjectId}>
            <option value="">Select project…</option>
            {projects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.key})
              </option>
            ))}
          </select>
        </div>
        <div className="field flex-1" style={{ minWidth: 160 }}>
          <label>Issue type</label>
          <select className="select" value={typeId} onChange={(e) => setTypeId(e.target.value)}>
            <option value="">Select…</option>
            {types.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label>Summary</label>
        <input className="input" autoFocus value={summary} onChange={(e) => setSummary(e.target.value)} placeholder="What needs to be done?" />
      </div>

      <div className="field">
        <label>Description</label>
        <textarea className="textarea" value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Add more detail…" />
      </div>

      <div className="row gap-16 wrap">
        <div className="field flex-1" style={{ minWidth: 160 }}>
          <label>Priority</label>
          <select className="select" value={priorityId} onChange={(e) => setPriorityId(e.target.value)}>
            <option value="">None</option>
            {priorities.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name}
              </option>
            ))}
          </select>
        </div>
        <div className="field" style={{ width: 120 }}>
          <label>Story points</label>
          <input className="input" type="number" min="0" value={storyPoints} onChange={(e) => setStoryPoints(e.target.value)} />
        </div>
        <div className="field flex-1" style={{ minWidth: 200 }}>
          <label>Assignee</label>
          <UserPicker value={assignee} onChange={setAssignee} />
        </div>
      </div>

      <div className="field">
        <label>Labels</label>
        <LabelInput value={labels} onChange={setLabels} />
      </div>

      <div className="field">
        <label>Components</label>
        <ComponentInput projectId={projectId} value={componentIds} onChange={setComponentIds} />
      </div>
    </Modal>
  );
}
