import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { OverviewStats } from '../../types';
import { getOverview } from '../../api/analytics';
import { SpinnerCenter } from '../../components/Spinner';
import { apiErrorMessage } from '../../api/client';
import { StatCard } from '../../components/charts/StatCard';
import { BarChart } from '../../components/charts/BarChart';
import { DonutChart } from '../../components/charts/DonutChart';
import { AttentionIssueRow } from '../../components/Attention';

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

  const needing = stats.projects.filter((p) => p.needs_attention);
  const topAttention = stats.top_attention || [];

  return (
    <div>
      <div className="section-head">
        <h2 className="section-head-title">Insights</h2>
        <p className="section-head-sub">Activity across every project in this instance.</p>
      </div>

      {/* ---- Action-first: attention rollup ---- */}
      <div className="insights-stats">
        <StatCard
          label="Projects needing attention"
          value={`${stats.projects_needing_attention} of ${stats.total_projects}`}
          accent={stats.projects_needing_attention > 0 ? 'red' : 'green'}
        />
        <StatCard label="Overdue" value={stats.total_overdue} accent={stats.total_overdue > 0 ? 'red' : 'slate'} />
        <StatCard
          label="High priority open"
          value={stats.total_high_priority_open}
          accent={stats.total_high_priority_open > 0 ? 'amber' : 'slate'}
        />
        <StatCard
          label="Unassigned open"
          value={stats.total_unassigned_open}
          accent={stats.total_unassigned_open > 0 ? 'amber' : 'slate'}
        />
        <StatCard label="Blocked" value={stats.total_blocked} accent={stats.total_blocked > 0 ? 'red' : 'slate'} />
        <StatCard
          label="Sprints at risk"
          value={stats.projects_at_risk}
          accent={stats.projects_at_risk > 0 ? 'red' : 'slate'}
        />
      </div>

      <div className="insights-grid insights-grid-2">
        <div className="chart-card">
          <h3 className="chart-title">Projects needing attention</h3>
          {needing.length === 0 ? (
            <div className="all-clear">
              <span className="all-clear-icon">✓</span>
              <div>
                <div className="all-clear-title">All projects are healthy</div>
                <div className="all-clear-sub">Nothing overdue, blocked, or at risk across the instance.</div>
              </div>
            </div>
          ) : (
            <div className="attn-proj-list">
              {needing.map((p) => (
                <Link key={p.project_id} to={`/projects/${p.project_key}/board`} className="attn-proj-row">
                  <span className="color-dot" style={{ background: colorFor(p.project_key, p.avatar_color) }} />
                  <div className="attn-proj-main">
                    <div className="attn-proj-head">
                      <span className="attn-proj-key">{p.project_key}</span>
                      <span className="muted attn-proj-name">{p.project_name}</span>
                      {p.at_risk_sprint && <span className="sev-badge sev-high">Sprint at risk</span>}
                    </div>
                    {p.top_reasons.length > 0 && (
                      <div className="attn-reasons">
                        {p.top_reasons.map((r) => (
                          <span key={r} className="attn-reason-chip">
                            {r}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                  <div className="attn-proj-counts">
                    {p.overdue > 0 && <span className="attn-mini sev-high">{p.overdue} overdue</span>}
                    {p.high_priority_open > 0 && <span className="attn-mini sev-medium">{p.high_priority_open} high</span>}
                    {p.blocked > 0 && <span className="attn-mini sev-high">{p.blocked} blocked</span>}
                    <span className="attn-score" title="Attention score">
                      {p.attention_score}
                    </span>
                  </div>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="chart-card">
          <h3 className="chart-title">Top issues needing attention</h3>
          {topAttention.length === 0 ? (
            <div className="chart-empty">No issues need attention.</div>
          ) : (
            <div className="attn-samples">
              {topAttention.map((i) => (
                <AttentionIssueRow key={i.key} issue={i} showProject />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ---- Descriptive overview (unchanged) ---- */}
      <div className="insights-stats mt-16">
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
