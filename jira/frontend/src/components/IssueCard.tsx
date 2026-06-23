import { IssueListItem } from '../types';
import { IssueTypeIcon } from './IssueTypeIcon';
import { PriorityIcon } from './PriorityIcon';
import { Avatar } from './Avatar';

interface Props {
  issue: IssueListItem;
  onClick?: () => void;
}

export function IssueCardContent({ issue }: { issue: IssueListItem }) {
  return (
    <>
      <div className="issue-card-summary">{issue.summary}</div>
      {issue.labels && issue.labels.length > 0 && (
        <div className="mb-8">
          {issue.labels.slice(0, 3).map((l) => (
            <span key={l} className="label-chip">
              {l}
            </span>
          ))}
        </div>
      )}
      <div className="issue-card-meta">
        <span className="issue-card-key">
          <IssueTypeIcon type={issue.type} />
          {issue.key}
        </span>
        <span className="issue-card-right">
          <PriorityIcon priority={issue.priority} />
          {issue.story_points != null && <span className="story-points">{issue.story_points}</span>}
          {issue.assignee && <Avatar user={issue.assignee} size={22} />}
        </span>
      </div>
    </>
  );
}

export function IssueCard({ issue, onClick }: Props) {
  return (
    <div className="issue-card" onClick={onClick}>
      <IssueCardContent issue={issue} />
    </div>
  );
}
