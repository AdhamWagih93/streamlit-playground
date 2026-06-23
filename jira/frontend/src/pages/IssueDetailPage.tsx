import { useParams, useNavigate } from 'react-router-dom';
import { IssueDetailView } from '../components/IssueDetailView';

export function IssueDetailPage() {
  const { issueKey } = useParams();
  const navigate = useNavigate();
  if (!issueKey) return null;
  return (
    <div className="page">
      <div className="breadcrumb">
        <span className="pointer" onClick={() => navigate(-1)}>
          ← Back
        </span>
      </div>
      <div className="card" style={{ padding: 24 }}>
        <IssueDetailView issueKey={issueKey} />
      </div>
    </div>
  );
}
