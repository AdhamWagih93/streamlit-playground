import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { OverviewStats } from '../../types';
import { getOverview } from '../../api/analytics';
import { SpinnerCenter } from '../../components/Spinner';
import { apiErrorMessage } from '../../api/client';
import { StatCard } from '../../components/charts/StatCard';
import { BarChart } from '../../components/charts/BarChart';
import { DonutChart } from '../../components/charts/DonutChart';

const COLORS = ['#6366f1', '#0ea5e9', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#ec4899'];
function colorFor(key: string, fallback?: string | null): string {
  if (fallback) return fallback;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return COLORS[h % COLORS.length];
}

function pct(rate: number): string {
  return `${Math.round((rate || 0) * 100)}%`;
}

export function Insights() {
  const [stats, setStats] = useState<OverviewStats | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  useEffect(() => {
    getOverview()
      .then(setStats)
      .catch((e) => setError(apiErrorMessage(e, 'Could not load insights')))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <SpinnerCenter />;
  if (error) return <div className="alert alert-error">{error}</div>;
  if (!stats) return null;

  return (
    <div>
      <div className="section-head">
        <h2 className="section-head-title">Insights</h2>
        <p className="section-head-sub">Activity across every project in this instance.</p>
      </div>

      <div className="insights-stats">
        <StatCard label="Projects" value={stats.total_projects} accent="indigo" />
        <StatCard label="Total issues" value={stats.total_issues} accent="slate" />
        <StatCard label="Open" value={stats.open_issues} accent="amber" />
        <StatCard label="Closed" value={stats.closed_issues} accent="green" />
        <StatCard label="Resolution rate" value={pct(stats.resolution_rate)} accent="blue" />
      </div>

      <div className="insights-grid">
        <div className="chart-card">
          <h3 className="chart-title">Issues by status</h3>
          <BarChart items={stats.by_status} />
        </div>
        <div className="chart-card">
          <h3 className="chart-title">Issues by type</h3>
          <DonutChart items={stats.by_type} centerLabel="issues" />
        </div>
      </div>

      <div className="chart-card mt-16">
        <h3 className="chart-title">Projects</h3>
        {stats.projects.length === 0 ? (
          <div className="chart-empty">No projects yet.</div>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>Project</th>
                <th style={{ width: 90 }}>Total</th>
                <th style={{ width: 80 }}>Open</th>
                <th style={{ width: 80 }}>Closed</th>
                <th style={{ width: 120 }}>Resolution</th>
                <th style={{ width: 130 }}>Avg velocity</th>
              </tr>
            </thead>
            <tbody>
              {stats.projects.map((p) => (
                <tr key={p.project_id}>
                  <td>
                    <Link to={`/projects/${p.project_key}/board`} className="row gap-8" style={{ color: 'inherit' }}>
                      <span className="color-dot" style={{ background: colorFor(p.project_key, p.avatar_color) }} />
                      <span style={{ fontWeight: 600 }}>{p.project_key}</span>
                      <span className="muted">{p.project_name}</span>
                    </Link>
                  </td>
                  <td>{p.total_issues}</td>
                  <td>{p.open_issues}</td>
                  <td>{p.closed_issues}</td>
                  <td>{pct(p.resolution_rate)}</td>
                  <td>{p.avg_velocity_points.toFixed(1)} pts</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
