import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';

type AuditLogEvent = {
  id: string;
  created_at: string;
  user_id?: string | null;
  user_email?: string | null;
  user_role?: string | null;
  method: string;
  route_path?: string | null;
  path: string;
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

type UserOption = {
  id: string;
  email: string;
};

const DEFAULT_PAGE_SIZE = 50;

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) return fallback;
  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  return responseDetail || (error as { message?: string }).message || fallback;
};

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

const AdminAuditLogsPage: React.FC = () => {
  const [page, setPage] = useState(1);
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

  const {
    data,
    isLoading,
    isFetching,
    error,
  } = useQuery<AuditLogPage>({
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

  const logs = data?.items ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / DEFAULT_PAGE_SIZE));

  const pageSummary = useMemo(() => {
    if (!total) return 'No matching events';
    const start = (page - 1) * DEFAULT_PAGE_SIZE + 1;
    const end = Math.min(page * DEFAULT_PAGE_SIZE, total);
    return `${start}-${end} of ${total}`;
  }, [page, total]);

  if (isLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading audit logs"
        message="Preparing API request and data-change history."
      />
    );
  }

  if (error) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load audit logs"
        message={getErrorMessage(error, 'The audit log history could not be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Audit logs</h1>
            <p className="catalog-card-copy">
              Browse request history, user activity, and inferred data updates.
            </p>
          </div>
          <div className="compact-toolbar dashboard-toolbar">
            <Link to="/admin/data" className="button-secondary hover:no-underline">
              Family & sample data
            </Link>
            <Link
              to="/admin/data/clickhouse"
              className="button-secondary hover:no-underline"
            >
              ClickHouse tables
            </Link>
            <Link to="/admin/data/presets" className="button-secondary hover:no-underline">
              Preset filters
            </Link>
            <Link to="/admin/data/tags" className="button-secondary hover:no-underline">
              Variant tags
            </Link>
          </div>
        </div>
      </section>

      <section className="surface-card space-y-4">
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
        <p className="table-subtle">
          {pageSummary}
          {isFetching ? ' (refreshing...)' : ''}
        </p>

        <div className="data-table-shell">
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
              {logs.length ? (
                logs.map((event) => (
                  <tr key={event.id}>
                    <td>{formatDateTime(event.created_at)}</td>
                    <td>{event.user_email || event.user_id || 'anonymous'}</td>
                    <td>{event.method}</td>
                    <td>{event.route_path || event.path}</td>
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
    </div>
  );
};

export default AdminAuditLogsPage;
