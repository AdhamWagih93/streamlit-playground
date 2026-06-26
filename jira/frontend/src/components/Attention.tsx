import { ReactNode } from 'react';
import { Link } from 'react-router-dom';
import { AttentionIssue, AttentionItem, AttentionSeverity, SprintHealth } from '../types';
import { formatDate } from '../lib/format';

// Severity palette per spec: high = red, medium = amber, low = slate.
export const SEVERITY_COLOR: Record<AttentionSeverity, string> = {
  high: '#dc2626',
  medium: '#d97706',
  low: '#64748b',
};

export function SeverityDot({ severity }: { severity: AttentionSeverity }) {
  return <span className="color-dot" style={{ background: SEVERITY_COLOR[severity] }} />;
}

export function SeverityBadge({ severity, children }: { severity: AttentionSeverity; children: ReactNode }) {
  return <span className={`sev-badge sev-${severity}`}>{children}</span>;
}

function PriorityChip({ priority, color }: { priority?: string | null; color?: string | null }) {
  if (!priority) return null;
  const c = color || '#64748b';
  return (
    <span className="prio-chip" style={{ color: c, borderColor: c }}>
      {priority}
    </span>
  );
}

function DueBadge({ issue }: { issue: AttentionIssue }) {
  if (issue.days_overdue != null && issue.days_overdue > 0) {
    return <span className="overdue-badge">{issue.days_overdue}d overdue</span>;
  }
  if (issue.due_date) {
    return <span className="due-badge">Due {formatDate(issue.due_date)}</span>;
  }
  return null;
}

// A single clickable issue row → /browse/{key}.
export function AttentionIssueRow({
  issue,
  showProject = false,
}: {
  issue: AttentionIssue;
  showProject?: boolean;
}) {
  const projectKey = showProject ? issue.key.split('-')[0] : null;
  return (
    <Link to={`/browse/${issue.key}`} className="attn-issue">
      {projectKey && <span className="attn-issue-proj">{projectKey}</span>}
      <span className="attn-issue-key">{issue.key}</span>
      <span className="attn-issue-summary" title={issue.summary}>
        {issue.summary}
      </span>
      <PriorityChip priority={issue.priority} color={issue.priority_color} />
      <span className={`attn-issue-assignee${issue.assignee ? '' : ' is-unassigned'}`}>
        {issue.assignee || 'Unassigned'}
      </span>
      <DueBadge issue={issue} />
    </Link>
  );
}

// A bucket of attention issues: left severity stripe, big count, samples.
export function AttentionCard({ item }: { item: AttentionItem }) {
  return (
    <div className={`attn-card sev-${item.severity}`}>
      <div className="attn-card-head">
        <span className="attn-count">{item.count}</span>
        <div className="attn-card-titles">
          <div className="attn-label">{item.label}</div>
          <div className="attn-desc">{item.description}</div>
        </div>
      </div>
      {item.samples.length > 0 && (
        <div className="attn-samples">
          {item.samples.slice(0, 5).map((s) => (
            <AttentionIssueRow key={s.key} issue={s} />
          ))}
        </div>
      )}
      {item.tql && item.count > item.samples.length && (
        <Link className="attn-viewall" to={`/search?tql=${encodeURIComponent(item.tql)}`}>
          View all {item.count} →
        </Link>
      )}
    </div>
  );
}

// Active-sprint health banner. Prominent (red) when at risk; calm otherwise.
export function SprintHealthCard({ health }: { health: SprintHealth }) {
  const pct = Math.round((health.percent_complete || 0) * 100);
  const daysLeft =
    health.days_remaining != null
      ? health.days_remaining <= 0
        ? 'ends today'
        : `${health.days_remaining}d left`
      : null;

  if (!health.at_risk) {
    return (
      <div className="sprint-health">
        <div className="sprint-health-row">
          <div className="sprint-health-name">{health.name}</div>
          <div className="sprint-health-meta">
            {pct}% complete{daysLeft ? ` · ${daysLeft}` : ''} · {health.incomplete_issues} left
          </div>
        </div>
        {health.goal && <div className="sprint-health-goal">{health.goal}</div>}
        <div className="progress-bar">
          <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
        </div>
      </div>
    );
  }

  return (
    <div className="sprint-health at-risk">
      <div className="sprint-health-row">
        <div className="sprint-health-name">
          <span className="sprint-risk-flag">⚠ At risk</span> {health.name}
        </div>
        <div className="sprint-health-meta">
          {pct}% complete{daysLeft ? ` · ${daysLeft}` : ''} · {health.incomplete_issues} incomplete
        </div>
      </div>
      {health.risk_reason && <div className="sprint-risk-reason">{health.risk_reason}</div>}
      <div className="progress-bar">
        <div
          className="progress-bar-fill"
          style={{ width: `${pct}%`, background: SEVERITY_COLOR.high }}
        />
      </div>
    </div>
  );
}
