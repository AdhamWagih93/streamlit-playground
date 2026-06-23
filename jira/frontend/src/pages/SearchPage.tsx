import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { IssueListItem, Page, SavedFilter } from '../types';
import { runSearch, validateTql, listFilters, createFilter, deleteFilter } from '../api/search';
import { IssueTypeIcon } from '../components/IssueTypeIcon';
import { PriorityIcon } from '../components/PriorityIcon';
import { StatusBadge } from '../components/StatusBadge';
import { Avatar } from '../components/Avatar';
import { Spinner } from '../components/Spinner';
import { EmptyState } from '../components/EmptyState';
import { IssueDetailModal } from '../components/IssueDetailModal';
import { timeAgo } from '../lib/format';
import { apiErrorMessage } from '../api/client';

const EXAMPLES = [
  'project = ENG AND status = "In Progress"',
  'assignee = currentUser() AND statusCategory != done ORDER BY updated DESC',
  'type = Bug AND priority IN (High, Highest)',
];

export function SearchPage() {
  const [searchParams] = useSearchParams();
  const initial = searchParams.get('q') || '';
  const [tql, setTql] = useState(initial);
  const [results, setResults] = useState<Page<IssueListItem> | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [validation, setValidation] = useState<{ valid: boolean; error?: string | null } | null>(null);
  const [filters, setFilters] = useState<SavedFilter[]>([]);
  const [openKey, setOpenKey] = useState<string | null>(null);
  const [page, setPage] = useState(1);

  function loadFilters() {
    listFilters().then(setFilters).catch(() => {});
  }
  useEffect(loadFilters, []);

  // Live validate (debounced).
  useEffect(() => {
    if (!tql.trim()) {
      setValidation(null);
      return;
    }
    const t = setTimeout(() => {
      validateTql(tql).then(setValidation).catch(() => setValidation(null));
    }, 350);
    return () => clearTimeout(t);
  }, [tql]);

  async function run(p = 1) {
    setLoading(true);
    setError('');
    setPage(p);
    try {
      const res = await runSearch(tql, p, 50);
      setResults(res);
    } catch (e) {
      setError(apiErrorMessage(e, 'Search failed'));
      setResults(null);
    } finally {
      setLoading(false);
    }
  }

  // Auto-run if arriving with a ?q= query.
  useEffect(() => {
    if (initial.trim()) run(1);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function save() {
    const name = prompt('Filter name');
    if (!name) return;
    const shared = confirm('Share this filter with your team? (OK = shared)');
    try {
      await createFilter({ name, query: tql, is_shared: shared });
      loadFilters();
    } catch (e) {
      setError(apiErrorMessage(e, 'Could not save filter'));
    }
  }

  const totalPages = results ? Math.max(1, Math.ceil(results.total / results.page_size)) : 1;

  return (
    <div className="page" style={{ maxWidth: '100%' }}>
      <div className="page-header">
        <div>
          <h1 className="page-title">Search issues</h1>
          <div className="page-subtitle">Query with TQL — Trackly Query Language</div>
        </div>
      </div>

      <div className="row gap-16 wrap" style={{ alignItems: 'flex-start' }}>
        <div className="flex-1" style={{ minWidth: 320 }}>
          <textarea
            className="textarea"
            style={{ fontFamily: 'monospace', minHeight: 70 }}
            value={tql}
            onChange={(e) => setTql(e.target.value)}
            placeholder={EXAMPLES[0]}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') run(1);
            }}
          />
          <div className="row gap-8 mt-8 wrap">
            <button className="btn btn-primary" onClick={() => run(1)} disabled={loading}>
              {loading ? <Spinner /> : 'Run'} <span className="text-xs">⌘⏎</span>
            </button>
            <button className="btn" onClick={save} disabled={!tql.trim()}>
              Save filter
            </button>
            {validation && (
              <span className={validation.valid ? 'text-sm' : 'text-sm'} style={{ color: validation.valid ? 'var(--green-500)' : 'var(--red-500)' }}>
                {validation.valid ? '✓ valid' : `✗ ${validation.error || 'invalid'}`}
              </span>
            )}
          </div>
          <div className="mt-8 text-xs muted">
            Examples:{' '}
            {EXAMPLES.map((ex, i) => (
              <span key={ex}>
                {i > 0 && ' · '}
                <a className="pointer" onClick={() => setTql(ex)}>
                  {ex}
                </a>
              </span>
            ))}
          </div>
        </div>

        <div style={{ width: 240 }}>
          <div className="detail-section">
            <h4>Saved filters</h4>
            {filters.length === 0 && <div className="muted text-sm">No saved filters yet.</div>}
            {filters.map((f) => (
              <div key={f.id} className="row-between" style={{ padding: '4px 0' }}>
                <a className="pointer flex-1" onClick={() => { setTql(f.query); run(1); }}>
                  {f.name} {f.is_shared && <span className="text-xs muted">(shared)</span>}
                </a>
                <button
                  className="btn btn-ghost btn-sm"
                  onClick={async () => {
                    await deleteFilter(f.id).catch(() => {});
                    loadFilters();
                  }}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        </div>
      </div>

      {error && <div className="alert alert-error mt-16">{error}</div>}

      <div className="card mt-16" style={{ overflow: 'hidden' }}>
        {results && results.items.length > 0 ? (
          <>
            <table className="data-table">
              <thead>
                <tr>
                  <th style={{ width: 40 }}>T</th>
                  <th style={{ width: 90 }}>Key</th>
                  <th>Summary</th>
                  <th style={{ width: 130 }}>Status</th>
                  <th style={{ width: 40 }}>P</th>
                  <th style={{ width: 160 }}>Assignee</th>
                  <th style={{ width: 90 }}>Updated</th>
                </tr>
              </thead>
              <tbody>
                {results.items.map((i) => (
                  <tr key={i.id} onClick={() => setOpenKey(i.key)}>
                    <td><IssueTypeIcon type={i.type} /></td>
                    <td className="nowrap">{i.key}</td>
                    <td>{i.summary}</td>
                    <td>{i.status && <StatusBadge status={i.status} />}</td>
                    <td><PriorityIcon priority={i.priority} /></td>
                    <td>
                      {i.assignee ? (
                        <span className="row gap-8">
                          <Avatar user={i.assignee} size={22} /> {i.assignee.display_name}
                        </span>
                      ) : (
                        <span className="muted">Unassigned</span>
                      )}
                    </td>
                    <td className="text-xs muted">{timeAgo(i.updated_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            <div className="row-between" style={{ padding: '10px 14px' }}>
              <span className="muted text-sm">{results.total} results</span>
              <div className="row gap-8">
                <button className="btn btn-sm" disabled={page <= 1} onClick={() => run(page - 1)}>
                  Prev
                </button>
                <span className="text-sm">
                  {page} / {totalPages}
                </span>
                <button className="btn btn-sm" disabled={page >= totalPages} onClick={() => run(page + 1)}>
                  Next
                </button>
              </div>
            </div>
          </>
        ) : results ? (
          <EmptyState icon="🔍" title="No matching issues" message="Try loosening your query." />
        ) : (
          <EmptyState icon="🔍" title="Run a query" message="Write a TQL query above and hit Run." />
        )}
      </div>

      {openKey && <IssueDetailModal issueKey={openKey} onClose={() => setOpenKey(null)} />}
    </div>
  );
}
