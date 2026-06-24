import { CountItem } from '../../types';
import { resolveColor } from './colors';

interface Props {
  items: CountItem[];
  /** Big number shown in the donut center; defaults to the total. */
  centerValue?: string | number;
  centerLabel?: string;
  size?: number;
  emptyLabel?: string;
}

const TWO_PI = Math.PI * 2;

/**
 * Hand-rolled SVG donut for a 2-3 slice breakdown (e.g. open vs closed).
 * Shows a percentage / total in the center and a legend with counts.
 */
export function DonutChart({ items, centerValue, centerLabel, size = 160, emptyLabel = 'No data yet' }: Props) {
  const total = items.reduce((n, i) => n + i.count, 0);
  const radius = size / 2;
  const stroke = Math.round(size * 0.16);
  const r = radius - stroke / 2;
  const circumference = TWO_PI * r;

  if (total === 0) {
    return <div className="chart-empty">{emptyLabel}</div>;
  }

  let offset = 0;
  const slices = items
    .filter((i) => i.count > 0)
    .map((item, i) => {
      const frac = item.count / total;
      const dash = frac * circumference;
      const seg = {
        color: resolveColor(item, i),
        dash,
        gap: circumference - dash,
        offset: -offset * circumference,
        label: item.label,
        count: item.count,
        pct: Math.round(frac * 100),
      };
      offset += frac;
      return seg;
    });

  return (
    <div className="donut-chart">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} role="img" aria-label="Breakdown chart">
        <g transform={`rotate(-90 ${radius} ${radius})`}>
          <circle cx={radius} cy={radius} r={r} fill="none" stroke="var(--slate-100)" strokeWidth={stroke} />
          {slices.map((s, i) => (
            <circle
              key={i}
              cx={radius}
              cy={radius}
              r={r}
              fill="none"
              stroke={s.color}
              strokeWidth={stroke}
              strokeDasharray={`${s.dash} ${s.gap}`}
              strokeDashoffset={s.offset}
              strokeLinecap="butt"
            >
              <title>{`${s.label}: ${s.count} (${s.pct}%)`}</title>
            </circle>
          ))}
        </g>
        <text x="50%" y="46%" textAnchor="middle" className="donut-center-value">
          {centerValue ?? total}
        </text>
        {centerLabel && (
          <text x="50%" y="60%" textAnchor="middle" className="donut-center-label">
            {centerLabel}
          </text>
        )}
      </svg>
      <ul className="donut-legend">
        {slices.map((s, i) => (
          <li key={i}>
            <span className="legend-dot" style={{ background: s.color }} />
            <span className="legend-label">{s.label}</span>
            <span className="legend-count">{s.count} · {s.pct}%</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
