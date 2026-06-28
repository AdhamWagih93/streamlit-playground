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
import { AttentionCard, SprintHealthCard } from '../components/Attention';
import { TimeFilter } from '../components/TimeFilter';
import { ExportMenu } from '../components/ExportMenu';
import { downloadExport } from '../api/download';

function pct(rate: number): string {
  return `${Math.round((rate || 0) * 100)}%`;
}

function windowLabel(start: string | null, end: string | null): string | null {
  if (!start && !end) return null;
  return `${start ?? '…'} → ${end ?? 'now'}`;
}

export function ProjectInsightsPage() {
  const { projectKey } = useParams();
  const [stats, setStats] = useState<ProjectStats | null>(null);
  const [period, setPeriod] = useState('all');
  const [loading, setLoading] = useState(true);
  const [forbidden, setForbidden] = useState(false);
  const [error, setError] = useState('');
  const [exporting, setExporting] = useState(false);

  async function exportInsights(format: string) {
    if (!projectKey) return;
    setExporting(true);
    setError('');
    try {
      await downloadExport(
        `/analytics/projects/${projectKey}/export`,
        { format, period },
        `${projectKey}-insights.${format}`
      );
    } catch (e) {
      setError(apiErrorMessage(e, 'Export failed'));
    } finally {
      setExporting(false);
    }
  }

  useEffect(() => {
    if (!projectKey) return;
    setLoading(true);
    setForbidden(false);
    setError('');
    getProjectStats(projectKey, { period })
      .then(setStats)
      .catch((e) => {
        if (axios.isAxiosError(e) && e.response?.status === 403) {
          setForbidden(true);
        } else {
          setError(apiErrorMessage(e, 'Could not load insights'));
        }
      })
      .finally(() => setLoading(false));
  }, [projectKey, period]);

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

  if (error && !stats) return <div className="page"><div className="alert alert-error">{error}</div></div>;
  if (!stats) return null;

  const openVsClosed = [
    { label: 'Open', count: stats.open_issues, color: '#f59e0b' },
    { label: 'In progress', count: stats.in_progress_issues, color: '#2563eb' },
    { label: 'Closed', count: stats.closed_issues, color: '#22c55e' },
  ];

  const sprint = stats.sprint_health;
  const attention = stats.attention || [];
  const allClear = attention.length === 0 && !(sprint && sprint.at_risk);

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
        <div className="row gap-8" style={{ alignItems: 'center' }}>
          <TimeFilter value={period} onChange={setPeriod} />
          <ExportMenu
            options={[
              { label: 'JSON', format: 'json' },
              { label: 'CSV', format: 'csv' },
              { label: 'Markdown', format: 'md' },
            ]}
            onSelect={exportInsights}
            busy={exporting}
          />
        </div>
      </div>

      {error && <div className="alert alert-error mt-16">{error}</div>}

      {/* ---- Action-first: what needs attention now ---- */}
      <section className="attn-section">
        <h2 className="attn-section-title">Needs attention</h2>
        <p className="window-note">Reflects current state — not affected by the time filter.</p>

        {sprint && <SprintHealthCard health={sprint} />}

        {allClear ? (
          <div className="all-clear">
            <span className="all-clear-icon">✓</span>
            <div>
              <div className="all-clear-title">Nothing needs attention right now</div>
              <div className="all-clear-sub">No overdue, blocked, or high-priority issues, and the sprint is on track.</div>
            </div>
          </div>
        ) : (
          attention.length > 0 && (
            <div className="attn-grid">
              {attention.map((item) => (
                <AttentionCard key={item.key} item={item} />
              ))}
            </div>
          )
        )}
      </section>

      {/* ---- Descriptive charts (scoped to the selected time window) ---- */}
      {period !== 'all' && windowLabel(stats.window.start, stats.window.end) && (
        <p className="window-note">Showing activity in {windowLabel(stats.window.start, stats.window.end)}</p>
      )}
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
        {stats.by_component.length > 0 && (
          <div className="chart-card">
            <h3 className="chart-title">Issues by component</h3>
            <BarChart items={stats.by_component} />
          </div>
        )}
      </div>

      <div className="chart-card mt-16">
        <h3 className="chart-title">Sprint velocity</h3>
        <VelocityChart points={stats.velocity} />
      </div>
    </div>
  );
}
