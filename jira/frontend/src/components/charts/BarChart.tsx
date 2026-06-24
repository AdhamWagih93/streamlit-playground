import { CountItem } from '../../types';
import { resolveColor } from './colors';

interface Props {
  items: CountItem[];
  /** Optional label shown when there is no data. */
  emptyLabel?: string;
}

/**
 * Hand-rolled horizontal bar chart. Each row shows a label, a proportional
 * bar (scaled to the largest count) and the count. Accessible via title attrs.
 */
export function BarChart({ items, emptyLabel = 'No data yet' }: Props) {
  const max = Math.max(1, ...items.map((i) => i.count));
  if (items.length === 0) {
    return <div className="chart-empty">{emptyLabel}</div>;
  }
  return (
    <div className="bar-chart" role="list">
      {items.map((item, i) => {
        const color = resolveColor(item, i);
        const pct = Math.round((item.count / max) * 100);
        return (
          <div className="bar-row" key={`${item.label}-${i}`} role="listitem" title={`${item.label}: ${item.count}`}>
            <div className="bar-label" title={item.label}>{item.label}</div>
            <div className="bar-track">
              <div className="bar-fill" style={{ width: `${pct}%`, background: color }} />
            </div>
            <div className="bar-count">{item.count}</div>
          </div>
        );
      })}
    </div>
  );
}
