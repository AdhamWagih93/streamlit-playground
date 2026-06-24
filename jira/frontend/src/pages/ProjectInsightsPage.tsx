import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import axios from 'axios';
import { ProjectStats } from '../types';
import { getProjectStats } from '../api/analytics';
import { SpinnerCenter } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { apiErrorMessage } from '../api/client';
import { StatCard } from '../components/charts/StatCard';
import { BarChart } from '../components/charts/BarChart';
import { DonutChart } from '../components/charts/DonutChart';
import { VelocityChart } from '../components/charts/VelocityChart';

function pct(rate: number): string {
  return `${Math.round((rate || 0) * 100)}%`;
}

export function ProjectInsightsPage() {
  const { projectKey } = useParams();
  const [stats, setStats] = useState<ProjectStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!projectKey) return;
    setLoading(true);
    setForbidden(false);
    setError('');
    getProjectStats(projectKey)
      .then(setStats)
      .catch((e) => {
        if (axios.isAxiosError(e) && e.response?.status === 403) {
          setForbidden(true);
        } else {
          setError(apiErrorMessage(e, 'Could not load insights'));
        }
      })
      .finally(() => setLoading(false));
  }, [projectKey]);

  if (loading) return <SpinnerCenter />;

  if (forbidden) {
    return (
      <div className="page">
        <div className="breadcrumb">{projectKey} / Insights</div>
        <EmptyState
          icon="🔒"
          title="No access to these insights"
          message="You need browse access to this project to view its analytics. Ask a project admin to add you in the People tab."
        />
      </div>
    );
  }

  if (error) return <div className="page"><div className="alert alert-error">{error}</div></div>;
  if (!stats) return null;

  const openVsClosed = [
    { label: 'Open', count: stats.open_issues, color: '#f59e0b' },
    { label: 'In progress', count: stats.in_progress_issues, color: '#2563eb' },
    { label: 'Closed', count: stats.closed_issues, color: '#22c55e' },
  ];

  return (
    <div className="page">
      <div className="breadcrumb">{stats.project_key} / Insights</div>
      <div className="page-header">
        <div>
          <h1 className="page-title">{stats.project_name} insights</h1>
          <div className="page-subtitle">
            Avg velocity {stats.avg_velocity_points.toFixed(1)} pts · {stats.avg_velocity_issues.toFixed(1)} issues per sprint
          </div>
        </div>
      </div>

      <div className="insights-stats">
        <StatCard label="Total issues" value={stats.total_issues} accent="slate" />
        <StatCard label="Open" value={stats.open_issues} accent="amber" />
        <StatCard label="In progress" value={stats.in_progress_issues} accent="blue" />
        <StatCard label="Closed" value={stats.closed_issues} accent="green" />
        <StatCard label="Resolution rate" value={pct(stats.resolution_rate)} accent="indigo" />
      </div>

      <div className="insights-grid">
        <div className="chart-card">
          <h3 className="chart-title">Progress</h3>
          <DonutChart items={openVsClosed} centerValue={pct(stats.resolution_rate)} centerLabel="resolved" />
        </div>
        <div className="chart-card">
          <h3 className="chart-title">Issues by status</h3>
          <BarChart items={stats.by_status} />
        </div>
        <div className="chart-card">
          <h3 className="chart-title">Issues by type</h3>
          <DonutChart items={stats.by_type} centerLabel="issues" />
        </div>
        <div className="chart-card">
          <h3 className="chart-title">Issues by priority</h3>
          <BarChart items={stats.by_priority} />
        </div>
      </div>

      <div className="chart-card mt-16">
        <h3 className="chart-title">Sprint velocity</h3>
        <VelocityChart points={stats.velocity} />
      </div>
    </div>
  );
}
