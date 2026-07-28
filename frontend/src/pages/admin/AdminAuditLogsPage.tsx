import React, { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { getErrorMessage } from '../../lib/errorMessage';

type AuditLogEvent = {
  id: string;
  created_at: string;
  user_id?: string | null;
  user_email?: string | null;
  user_role?: string | null;
  method: string;
  route_path?: string | null;
  path: string;
  query_string?: string | null;
  status_code: number;
  duration_ms: number;
  remote_ip?: string | null;
  request_body?: unknown;
  db_update?: Record<string, unknown> | null;
  error?: string | null;
};

type AuditLogPage = {
  page: number;
  page_size: number;
  total: number;
  items: AuditLogEvent[];
};

type UiEvent = {
  id: string;
  created_at: string;
  user_email?: string | null;
  user_id?: string | null;
  event_type: string;
  category?: string | null;
  label?: string | null;
  target_id?: string | null;
  path?: string | null;
  to_path?: string | null;
  href?: string | null;
};

type UiEventPage = {
  page: number;
  page_size: number;
  total: number;
  items: UiEvent[];
};

type UserOption = {
  id: string;
  email: string;
};

type AuditView = 'requests' | 'interactions';

const DEFAULT_PAGE_SIZE = 50;

const formatDateTime = (iso: string) => {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString();
};

const summarizeDbUpdate = (dbUpdate?: Record<string, unknown> | null) => {
  if (!dbUpdate) return 'n/a';
  const updateType = String(dbUpdate.updateType ?? '').trim();
  const entity = String(dbUpdate.dbEntity ?? '').trim();
  const entityId = String(dbUpdate.entityId ?? '').trim();
  if (!updateType && !entity) return 'n/a';
  return [updateType, entity, entityId].filter(Boolean).join(' ');
};

const describeLocation = (event: UiEvent): string => {
  if (event.event_type === 'navigation') {
    const from = event.path || '—';
    const to = event.to_path || '—';
    return `${from} → ${to}`;
  }
  return event.href || event.path || '—';
};

const AdminAuditLogsPage: React.FC = () => {
  const [view, setView] = useState<AuditView>('requests');
  const [page, setPage] = useState(1);

  // API request filters
  const [draftMethod, setDraftMethod] = useState('');
  const [draftStatusCode, setDraftStatusCode] = useState('');
  const [draftUserEmail, setDraftUserEmail] = useState('');
  const [draftPathContains, setDraftPathContains] = useState('');
  const [appliedFilters, setAppliedFilters] = useState({
    method: '',
    statusCode: '',
    userEmail: '',
    pathContains: '',
  });

  // UI interaction filters
  const [draftEventType, setDraftEventType] = useState('');
  const [draftIntUserEmail, setDraftIntUserEmail] = useState('');
  const [draftIntSearch, setDraftIntSearch] = useState('');
  const [appliedIntFilters, setAppliedIntFilters] = useState({
    eventType: '',
    userEmail: '',
    search: '',
  });

  const requestsQuery = useQuery<AuditLogPage>({
    queryKey: [
      'admin',
      'audit-logs',
      page,
      appliedFilters.method,
      appliedFilters.statusCode,
      appliedFilters.userEmail,
      appliedFilters.pathContains,
    ],
    queryFn: async () => {
      const response = await api.get('/admin/audit-logs', {
        params: {
          page,
          page_size: DEFAULT_PAGE_SIZE,
          method: appliedFilters.method || undefined,
          status_code: appliedFilters.statusCode
            ? Number(appliedFilters.statusCode)
            : undefined,
          user_email: appliedFilters.userEmail.trim() || undefined,
          path_contains: appliedFilters.pathContains.trim() || undefined,
        },
      });
      return response.data as AuditLogPage;
    },
    enabled: view === 'requests',
    retry: false,
  });

  const interactionsQuery = useQuery<UiEventPage>({
    queryKey: [
      'admin',
      'ui-events',
      page,
      appliedIntFilters.eventType,
      appliedIntFilters.userEmail,
      appliedIntFilters.search,
    ],
    queryFn: async () => {
      const response = await api.get('/admin/ui-events', {
        params: {
          page,
          page_size: DEFAULT_PAGE_SIZE,
          event_type: appliedIntFilters.eventType || undefined,
          user_email: appliedIntFilters.userEmail.trim() || undefined,
          path_contains: appliedIntFilters.search.trim() || undefined,
        },
      });
      return response.data as UiEventPage;
    },
    enabled: view === 'interactions',
    retry: false,
  });

  const { data: users = [] } = useQuery<UserOption[]>({
    queryKey: ['admin', 'users'],
    queryFn: async () => {
      const response = await api.get('/auth/users');
      const payload = response.data as Array<{ id: string; email: string }>;
      return payload.map((entry) => ({ id: String(entry.id), email: entry.email }));
    },
    retry: false,
  });

  const activeQuery = view === 'requests' ? requestsQuery : interactionsQuery;
  const total = activeQuery.data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));

  const pageSummary = useMemo(() => {
    if (!total) return 'No matching events';
    const start = (page - 1) * DEFAULT_PAGE_SIZE + 1;
    const end = Math.min(page * DEFAULT_PAGE_SIZE, total);
    return `${start}-${end} of ${total}`;
  }, [page, total]);

  const switchView = (next: AuditView) => {
    if (next === view) return;
    setView(next);
    setPage(1);
  };

  const requestLogs = requestsQuery.data?.items ?? [];
  const interactionLogs = interactionsQuery.data?.items ?? [];

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Audit logs</h1>
            <p className="catalog-card-copy">
              Browse API request history, inferred data updates, and client-side interactions.
            </p>
          </div>
          <div className="compact-toolbar dashboard-toolbar">
            <Link to="/admin/data/families" className="button-secondary hover:no-underline">
              Family & sample data
            </Link>
            <Link
              to="/admin/operations/clickhouse"
              className="button-secondary hover:no-underline"
            >
              ClickHouse tables
            </Link>
            <Link to="/admin/variants/presets" className="button-secondary hover:no-underline">
              Preset filters
            </Link>
            <Link to="/admin/variants/tags" className="button-secondary hover:no-underline">
              Variant tags
            </Link>
          </div>
        </div>
        <div className="compact-toolbar dashboard-toolbar">
          <button
            type="button"
            className={view === 'requests' ? 'button-secondary' : 'button-ghost'}
            aria-pressed={view === 'requests'}
            onClick={() => switchView('requests')}
          >
            API requests
          </button>
          <button
            type="button"
            className={view === 'interactions' ? 'button-secondary' : 'button-ghost'}
            aria-pressed={view === 'interactions'}
            onClick={() => switchView('interactions')}
          >
            UI interactions
          </button>
        </div>
      </section>

      {activeQuery.error ? (
        <PageState
          kicker="Administration"
          title="Could not load audit logs"
          message={getErrorMessage(activeQuery.error, 'The audit log history could not be loaded.')}
        />
      ) : (
        <section className="surface-card space-y-4">
          {view === 'requests' ? (
            <div className="variant-search-toolbar">
              <select
                aria-label="Filter method"
                value={draftMethod}
                onChange={(event) => setDraftMethod(event.target.value)}
              >
                <option value="">All methods</option>
                <option value="GET">GET</option>
                <option value="POST">POST</option>
                <option value="PUT">PUT</option>
                <option value="PATCH">PATCH</option>
                <option value="DELETE">DELETE</option>
              </select>
              <select
                aria-label="Filter status"
                value={draftStatusCode}
                onChange={(event) => setDraftStatusCode(event.target.value)}
              >
                <option value="">All status codes</option>
                <option value="200">200</option>
                <option value="201">201</option>
                <option value="204">204</option>
                <option value="400">400</option>
                <option value="401">401</option>
                <option value="403">403</option>
                <option value="404">404</option>
                <option value="500">500</option>
              </select>
              <select
                aria-label="Filter user"
                value={draftUserEmail}
                onChange={(event) => setDraftUserEmail(event.target.value)}
              >
                <option value="">All users</option>
                {users.map((user) => (
                  <option key={user.id} value={user.email}>
                    {user.email}
                  </option>
                ))}
              </select>
              <input
                aria-label="Filter path"
                placeholder="Path contains"
                value={draftPathContains}
                onChange={(event) => setDraftPathContains(event.target.value)}
              />
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setAppliedFilters({
                    method: draftMethod,
                    statusCode: draftStatusCode,
                    userEmail: draftUserEmail,
                    pathContains: draftPathContains,
                  });
                  setPage(1);
                }}
              >
                Apply filters
              </button>
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setDraftMethod('');
                  setDraftStatusCode('');
                  setDraftUserEmail('');
                  setDraftPathContains('');
                  setAppliedFilters({
                    method: '',
                    statusCode: '',
                    userEmail: '',
                    pathContains: '',
                  });
                  setPage(1);
                }}
              >
                Clear filters
              </button>
            </div>
          ) : (
            <div className="variant-search-toolbar">
              <select
                aria-label="Filter event type"
                value={draftEventType}
                onChange={(event) => setDraftEventType(event.target.value)}
              >
                <option value="">All event types</option>
                <option value="click">click</option>
                <option value="navigation">navigation</option>
                <option value="submit">submit</option>
                <option value="view">view</option>
                <option value="query">query</option>
              </select>
              <select
                aria-label="Filter user"
                value={draftIntUserEmail}
                onChange={(event) => setDraftIntUserEmail(event.target.value)}
              >
                <option value="">All users</option>
                {users.map((user) => (
                  <option key={user.id} value={user.email}>
                    {user.email}
                  </option>
                ))}
              </select>
              <input
                aria-label="Filter label or path"
                placeholder="Label or path contains"
                value={draftIntSearch}
                onChange={(event) => setDraftIntSearch(event.target.value)}
              />
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setAppliedIntFilters({
                    eventType: draftEventType,
                    userEmail: draftIntUserEmail,
                    search: draftIntSearch,
                  });
                  setPage(1);
                }}
              >
                Apply filters
              </button>
              <button
                type="button"
                className="button-secondary"
                onClick={() => {
                  setDraftEventType('');
                  setDraftIntUserEmail('');
                  setDraftIntSearch('');
                  setAppliedIntFilters({ eventType: '', userEmail: '', search: '' });
                  setPage(1);
                }}
              >
                Clear filters
              </button>
            </div>
          )}

          <p className="table-subtle">
            {activeQuery.isLoading ? 'Loading…' : pageSummary}
            {activeQuery.isFetching && !activeQuery.isLoading ? ' (refreshing...)' : ''}
          </p>

          <div className="data-table-shell">
            {view === 'requests' ? (
              <table className="analysis-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Method</th>
                    <th>Path</th>
                    <th>Status</th>
                    <th>Duration</th>
                    <th>Data update</th>
                    <th>Error</th>
                  </tr>
                </thead>
                <tbody>
                  {requestLogs.length ? (
                    requestLogs.map((event) => (
                      <tr key={event.id}>
                        <td>{formatDateTime(event.created_at)}</td>
                        <td>{event.user_email || event.user_id || 'anonymous'}</td>
                        <td>{event.method}</td>
                        <td>
                          {event.route_path || event.path}
                          {event.query_string ? (
                            <span className="table-subtle"> ?{event.query_string}</span>
                          ) : null}
                        </td>
                        <td>{event.status_code}</td>
                        <td>{event.duration_ms} ms</td>
                        <td>{summarizeDbUpdate(event.db_update)}</td>
                        <td>{event.error || '—'}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={8} className="table-empty">
                        No audit log entries found for the current filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            ) : (
              <table className="analysis-table">
                <thead>
                  <tr>
                    <th>Time</th>
                    <th>User</th>
                    <th>Type</th>
                    <th>Category</th>
                    <th>Label</th>
                    <th>Location</th>
                  </tr>
                </thead>
                <tbody>
                  {interactionLogs.length ? (
                    interactionLogs.map((event) => (
                      <tr key={event.id}>
                        <td>{formatDateTime(event.created_at)}</td>
                        <td>{event.user_email || event.user_id || 'anonymous'}</td>
                        <td>{event.event_type}</td>
                        <td>{event.category || '—'}</td>
                        <td>{event.label || '—'}</td>
                        <td>{describeLocation(event)}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={6} className="table-empty">
                        No interaction events found for the current filters.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            )}
          </div>

          <div className="compact-toolbar dashboard-toolbar">
            <button
              type="button"
              className="button-secondary"
              onClick={() => setPage((current) => Math.max(1, current - 1))}
              disabled={page <= 1}
            >
              Previous
            </button>
            <span className="badge-chip">
              Page {page} / {totalPages}
            </span>
            <button
              type="button"
              className="button-secondary"
              onClick={() => setPage((current) => Math.min(totalPages, current + 1))}
              disabled={page >= totalPages}
            >
              Next
            </button>
          </div>
        </section>
      )}
    </div>
  );
};

export default AdminAuditLogsPage;
