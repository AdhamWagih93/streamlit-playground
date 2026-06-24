import { VelocityPoint } from '../../types';

interface Props {
  points: VelocityPoint[];
  emptyLabel?: string;
}

/**
 * Grouped bars per sprint: committed vs completed points. Hand-rolled with
 * flex columns so it stays responsive without any chart library.
 */
export function VelocityChart({ points, emptyLabel = 'No sprint history yet' }: Props) {
  if (points.length === 0) {
    return <div className="chart-empty">{emptyLabel}</div>;
  }
  const max = Math.max(1, ...points.flatMap((p) => [p.committed_points, p.completed_points]));

  return (
    <div className="velocity-chart">
      <div className="velocity-bars">
        {points.map((p) => {
          const committedH = Math.round((p.committed_points / max) * 100);
          const completedH = Math.round((p.completed_points / max) * 100);
          return (
            <div className="velocity-group" key={p.sprint_id} title={p.sprint_name}>
              <div className="velocity-pair">
                <div
                  className="velocity-bar velocity-committed"
                  style={{ height: `${committedH}%` }}
                  title={`Committed: ${p.committed_points} pts`}
                />
                <div
                  className="velocity-bar velocity-completed"
                  style={{ height: `${completedH}%` }}
                  title={`Completed: ${p.completed_points} pts (${p.completed_issues} issues)`}
                />
              </div>
              <div className="velocity-x" title={p.sprint_name}>{p.sprint_name}</div>
            </div>
          );
        })}
      </div>
      <div className="velocity-legend">
        <span><span className="legend-dot velocity-committed" /> Committed</span>
        <span><span className="legend-dot velocity-completed" /> Completed</span>
      </div>
    </div>
  );
}
