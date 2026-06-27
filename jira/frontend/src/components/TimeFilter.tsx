// Reusable period selector that scopes the descriptive analytics stats.
// Controlled via `value` + `onChange`; emits backend period shortcuts
// (`all`/`7d`/`30d`/`90d`/`1y`).

export const TIME_FILTER_OPTIONS: { value: string; label: string }[] = [
  { value: 'all', label: 'All time' },
  { value: '7d', label: 'Last 7 days' },
  { value: '30d', label: 'Last 30 days' },
  { value: '90d', label: 'Last 90 days' },
  { value: '1y', label: 'Last year' },
];

interface TimeFilterProps {
  value: string;
  onChange: (period: string) => void;
}

export function TimeFilter({ value, onChange }: TimeFilterProps) {
  return (
    <div className="time-filter" role="group" aria-label="Time window">
      {TIME_FILTER_OPTIONS.map((opt) => (
        <button
          key={opt.value}
          type="button"
          className={`time-filter-opt${opt.value === value ? ' is-active' : ''}`}
          aria-pressed={opt.value === value}
          onClick={() => onChange(opt.value)}
        >
          {opt.label}
        </button>
      ))}
    </div>
  );
}
