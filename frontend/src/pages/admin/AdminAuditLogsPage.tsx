import React, { useMemo, useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import type {
  AuditLogEventOut,
  AuditLogPageOut,
  UiEventOut,
  UiEventPageOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { formatDateTime } from '../../lib/format';
import { userAccountsQuery } from '../../lib/users';

type AuditView = 'requests' | 'interactions';

const DEFAULT_PAGE_SIZE = 50;

const summarizeDbUpdate = (dbUpdate?: Record<string, unknown> | null) => {
  if (!dbUpdate) return 'n/a';
  const updateType = String(dbUpdate.updateType ?? '').trim();
  const entity = String(dbUpdate.dbEntity ?? '').trim();
  const entityId = String(dbUpdate.entityId ?? '').trim();
  if (!updateType && !entity) return 'n/a';
  return [updateType, entity, entityId].filter(Boolean).join(' ');
};

const describeLocation = (event: UiEventOut): string => {
  if (event.event_type === 'navigation') {
    const from = event.path || '—';
    const to = event.to_path || '—';
    return `${from} → ${to}`;
  }
  return event.href || event.path || '—';
};

// The flags the writers set in a row: all the page shows of a request's request_meta (which
// holds request headers besides) or a UI event's detail (which the client wrote).
type AuditFlag = { key: string; text: string; title: string };

type FlagSource = Record<string, unknown> | null | undefined;

// request_meta.record_hidden: answered as for an unknown record, which exists outside the
// caller's projects (main._answer_record_not_visible). Its value names the kind.
const recordHiddenFlag = (meta: FlagSource): AuditFlag | null => {
  const kind = meta?.record_hidden;
  if (!kind) return null;
  const noun = (typeof kind === 'string' && kind.trim()) || 'record';
  return {
    key: 'record-hidden',
    text: `${noun.charAt(0).toUpperCase()}${noun.slice(1)} outside the user's projects`,
    title: `Answered as for an unknown ${noun}: it exists outside the projects of the user who asked.`,
  };
};

// _escaped: the columns in which a value the database cannot store was written as an escape
// (core/pg_storable.py). A flag that is not a list of columns is shown without them.
const escapedFlag = (meta: FlagSource): AuditFlag | null => {
  const escaped = meta?._escaped;
  if (!escaped || (Array.isArray(escaped) && escaped.length === 0)) return null;
  const columns = Array.isArray(escaped)
    ? escaped.filter((column): column is string => typeof column === 'string')
    : [];
  return {
    key: 'escaped',
    text: columns.length
      ? `Escaped: ${columns.map((column) => column.replace(/_/g, ' ')).join(', ')}`
      : 'Escaped',
    title:
      'A value the database cannot store (a NUL character, half a surrogate pair, NaN or ' +
      'Infinity) was saved as a visible escape, so these columns are not verbatim.',
  };
};

// request_meta.emergency_sign_in: the emergency administrator's password sign-in beside
// Microsoft sign-in, or a request made with it (SEC-4).
const emergencySignInFlag = (meta: FlagSource): AuditFlag | null =>
  meta?.emergency_sign_in === true
    ? {
        key: 'emergency-sign-in',
        text: 'Emergency sign-in',
        title:
          'Signed in with the emergency administrator’s password beside Microsoft sign-in ' +
          '(AZURE_ADMIN_OVERRIDE on).',
      }
    : null;

type FieldChange = { from?: unknown; to?: unknown };

const isFieldChange = (value: unknown): value is FieldChange =>
  typeof value === 'object' && value !== null && 'to' in value;

// request_meta.account_change: the account this request made (a first Microsoft sign-in) or
// whose role or activation it changed, each field from and to (SEC-4).
const accountChangeFlag = (meta: FlagSource): AuditFlag | null => {
  const change = meta?.account_change;
  if (typeof change !== 'object' || change === null) return null;
  const { email, created, role, is_active: isActive } = change as Record<string, unknown>;
  const account = typeof email === 'string' && email ? email : 'an account';
  const parts: string[] = [];
  if (created === 'microsoft_sign_in') parts.push('made at its first Microsoft sign-in');
  if (isFieldChange(role) && !created) parts.push(`role ${String(role.from)} → ${String(role.to)}`);
  if (isFieldChange(isActive)) parts.push(isActive.to ? 'activated' : 'deactivated');
  if (!parts.length) return null;
  return {
    key: 'account-change',
    text: `Account ${account}: ${parts.join(', ')}`,
    title: 'This request made or changed a user account; the audit row keeps each field from and to.',
  };
};

const requestFlags = (event: AuditLogEventOut): AuditFlag[] =>
  [
    recordHiddenFlag(event.request_meta),
    escapedFlag(event.request_meta),
    emergencySignInFlag(event.request_meta),
    accountChangeFlag(event.request_meta),
  ].filter((flag): flag is AuditFlag => flag !== null);

// In a UI event's detail only _escaped and _deleted_user_id are the writer's (it drops a
// client's own): anything else there, a record_hidden included, came from the browser.
const uiEventFlags = (event: UiEventOut): AuditFlag[] => {
  const flag = escapedFlag(event.detail);
  return flag ? [flag] : [];
};

const AuditFlags: React.FC<{ flags: AuditFlag[] }> = ({ flags }) =>
  flags.length ? (
    <div className="table-chip-list">
      {flags.map((flag) => (
        <span key={flag.key} className="table-chip table-chip--warning" title={flag.title}>
          {flag.text}
        </span>
      ))}
    </div>
  ) : (
    <>—</>
  );

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

  const requestsQuery = useQuery<AuditLogPageOut>({
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
      return response.data as AuditLogPageOut;
    },
    enabled: view === 'requests',
    retry: false,
  });

  const interactionsQuery = useQuery<UiEventPageOut>({
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
      return response.data as UiEventPageOut;
    },
    enabled: view === 'interactions',
    retry: false,
  });

  // Whole accounts, not only their e-mail: the Users and Projects pages read this cache entry too.
  const { data: users = [] } = useQuery({ ...userAccountsQuery, retry: false });

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
                    <th>Flags</th>
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
                        <td>
                          <AuditFlags flags={requestFlags(event)} />
                        </td>
                        <td>{event.duration_ms} ms</td>
                        <td>{summarizeDbUpdate(event.db_update)}</td>
                        <td>{event.error || '—'}</td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={9} className="table-empty">
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
                    <th>Flags</th>
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
                        <td>
                          <AuditFlags flags={uiEventFlags(event)} />
                        </td>
                      </tr>
                    ))
                  ) : (
                    <tr>
                      <td colSpan={7} className="table-empty">
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
