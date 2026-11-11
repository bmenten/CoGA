import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi, type Mock } from 'vitest';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import AdminAuditLogsPage from '../AdminAuditLogsPage';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
  },
}));

// A request row as /admin/audit-logs serves it; request_meta holds its headers besides the flags.
const requestRow = (id: string, routePath: string, overrides: Record<string, unknown>) => ({
  id,
  created_at: '2026-04-28T20:00:00Z',
  user_email: 'viewer@example.com',
  method: 'GET',
  route_path: routePath,
  path: routePath,
  status_code: 200,
  duration_ms: 12,
  db_update: null,
  error: null,
  request_meta: {
    headers: { 'content-type': 'application/x-synthetic-type', accept: 'text/x-synthetic' },
  },
  ...overrides,
});

const uiEventRow = (id: string, label: string, detail: Record<string, unknown>, overrides: Record<string, unknown> = {}) => ({
  id,
  created_at: '2026-04-28T20:00:00Z',
  user_email: 'viewer@example.com',
  event_type: 'click',
  category: 'button',
  label,
  path: '/families',
  detail,
  ...overrides,
});

const page = (items: unknown[], total = items.length) => ({ page: 1, page_size: 50, total, items });

/** Serves the accounts, the request rows and the UI interactions; any other GET fails the test. */
const serve = (
  requests: unknown[] | (() => Promise<unknown>),
  uiEvents: unknown[] = [],
  users: unknown[] = [],
) => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/auth/users') return Promise.resolve({ data: users });
    if (url === '/admin/audit-logs') {
      return typeof requests === 'function' ? requests() : Promise.resolve({ data: page(requests) });
    }
    if (url === '/admin/ui-events') return Promise.resolve({ data: page(uiEvents) });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <AdminAuditLogsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );

// The params of the last request for `url`.
const lastParams = (url: string) =>
  (api.get as unknown as Mock).mock.calls.filter(([requested]) => requested === url).at(-1)?.[1]?.params;

describe('AdminAuditLogsPage', () => {
  it('renders audit log entries and applies method filter', async () => {
    serve(
      [
        requestRow('evt-1', '/families/{family_id}/small-variant-tags/{tag_key}', {
          user_email: 'admin@example.com',
          method: 'PATCH',
          path: '/families/F1/small-variant-tags/review',
          duration_ms: 23,
          db_update: {
            dbEntity: 'families',
            entityId: 'F1',
            updateType: 'update',
          },
        }),
      ],
      [],
      [{ id: 'u1', email: 'admin@example.com' }],
    );

    renderPage();

    expect(await screen.findByText('Audit logs')).toBeInTheDocument();
    expect((await screen.findAllByText('admin@example.com')).length).toBeGreaterThan(0);
    expect(screen.getByText('update families F1')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/filter user/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByLabelText(/filter method/i), {
      target: { value: 'PATCH' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply filters/i }));

    await waitFor(() => {
      expect(api.get).toHaveBeenCalledWith(
        '/admin/audit-logs',
        expect.objectContaining({
          params: expect.objectContaining({
            method: 'PATCH',
            user_email: 'admin@example.com',
            page: 1,
            page_size: 50,
          }),
        })
      );
    });
  });

  it('applies the status and path filters, pages through the rows, and clears every filter at once', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) =>
      url === '/admin/audit-logs'
        ? Promise.resolve({ data: page([requestRow('evt-1', '/api/projects', {})], 120) })
        : Promise.resolve({ data: [] }),
    );
    renderPage();

    expect(await screen.findByText('1-50 of 120')).toBeInTheDocument();
    expect(screen.getByText('Page 1 / 3')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(lastParams('/admin/audit-logs')).toMatchObject({ page: 2 }));
    expect(await screen.findByText('51-100 of 120')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('Filter status'), { target: { value: '404' } });
    fireEvent.change(screen.getByLabelText('Filter path'), { target: { value: '  /families  ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    // A filter starts at the first page again, the path trimmed and the status a number.
    await waitFor(() =>
      expect(lastParams('/admin/audit-logs')).toEqual({
        page: 1,
        page_size: 50,
        method: undefined,
        status_code: 404,
        user_email: undefined,
        path_contains: '/families',
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() => expect(lastParams('/admin/audit-logs')).toMatchObject({ status_code: undefined, path_contains: undefined }));
    expect(screen.getByLabelText('Filter status')).toHaveValue('');
    expect(screen.getByLabelText('Filter path')).toHaveValue('');
  });

  // An audit trail that could not be read is not an empty one.
  it('says the audit log could not be loaded, with the reason, never that nothing matches', async () => {
    serve(() => Promise.reject({ response: { status: 503, data: { detail: 'Audit store unavailable' } } }));
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Could not load audit logs' })).toBeInTheDocument();
    expect(screen.getByText('Audit store unavailable')).toBeInTheDocument();
    expect(screen.queryByText('No matching events')).not.toBeInTheDocument();
    expect(screen.queryByText(/No audit log entries found/)).not.toBeInTheDocument();
  });

  it('filters the UI interactions by type, user and text, and shows where a navigation went', async () => {
    serve(
      [],
      [
        uiEventRow('ui-nav', 'Open family', {}, { event_type: 'navigation', path: '/families', to_path: '/families/F1' }),
        uiEventRow('ui-link', 'Docs', {}, { category: null, path: '/dashboard', href: '/docs' }),
      ],
      [{ id: 'u1', email: 'viewer@example.com' }],
    );
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: 'UI interactions' }));
    const navigation = (await screen.findByText('Open family')).closest('tr') as HTMLElement;
    expect(navigation).toHaveTextContent('/families → /families/F1');
    const link = screen.getByText('Docs').closest('tr') as HTMLElement;
    // A link's own target, where it has one; no category is a dash.
    expect(within(link).getByText('/docs')).toBeInTheDocument();
    const [, , , category] = within(link).getAllByRole('cell');
    expect(category).toHaveTextContent(/^—$/);

    fireEvent.change(screen.getByLabelText('Filter event type'), { target: { value: 'navigation' } });
    fireEvent.change(screen.getByLabelText('Filter user'), { target: { value: 'viewer@example.com' } });
    fireEvent.change(screen.getByLabelText('Filter label or path'), { target: { value: ' family ' } });
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() =>
      expect(lastParams('/admin/ui-events')).toEqual({
        page: 1,
        page_size: 50,
        event_type: 'navigation',
        user_email: 'viewer@example.com',
        path_contains: 'family',
      }),
    );

    fireEvent.click(screen.getByRole('button', { name: 'Clear filters' }));
    await waitFor(() =>
      expect(lastParams('/admin/ui-events')).toMatchObject({ event_type: undefined, user_email: undefined, path_contains: undefined }),
    );
    expect(screen.getByLabelText('Filter event type')).toHaveValue('');
  });
});

// AUDIT-4: what the writers flag in a row, shown on the page. A request answered as for an
// unknown record carries request_meta.record_hidden; a row in which a value the database
// cannot store was escaped names its columns in request_meta._escaped, or for a UI event in
// detail._escaped. The page shows these flags and nothing else of request_meta or detail.
describe('AdminAuditLogsPage flags', () => {
  // The Flags cell of the row whose path or label is `text`.
  const flagsOf = (text: string): HTMLElement => {
    const row = screen.getByText(text).closest('tr');
    if (!row) throw new Error(`no row for ${text}`);
    const headers = within(row.closest('table') as HTMLElement).getAllByRole('columnheader');
    const column = headers.findIndex((header) => header.textContent === 'Flags');
    if (column < 0) throw new Error('no Flags column');
    return within(row).getAllByRole('cell')[column];
  };

  it('flags a request answered as for an unknown record, and one stored with escaped values', async () => {
    serve([
      requestRow('evt-hidden', '/api/families/{family_id}', {
        status_code: 404,
        request_meta: { headers: { accept: 'text/x-synthetic' }, record_hidden: 'family' },
      }),
      requestRow('evt-escaped', '/api/families/{family_id}/hpo', {
        method: 'POST',
        request_meta: { headers: {}, _escaped: ['path', 'query_string', 'request_body'] },
      }),
      requestRow('evt-clean', '/api/projects', {}),
    ]);
    renderPage();
    await screen.findByText('/api/projects');

    const hidden = within(flagsOf('/api/families/{family_id}')).getByText(
      "Family outside the user's projects",
    );
    expect(hidden).toHaveAttribute(
      'title',
      'Answered as for an unknown family: it exists outside the projects of the user who asked.',
    );
    expect(
      within(flagsOf('/api/families/{family_id}/hpo')).getByText(
        'Escaped: path, query string, request body',
      ),
    ).toHaveAttribute('title', expect.stringMatching(/^A value the database cannot store/));
    expect(flagsOf('/api/projects')).toHaveTextContent(/^—$/);

    // Nothing else of request_meta: its headers stay off the page.
    expect(screen.queryByText(/x-synthetic/)).not.toBeInTheDocument();
  });

  it('names the kind of record answered as unknown, and still shows a flag it cannot read', async () => {
    serve([
      requestRow('evt-sample', '/api/samples/{sample_id}/coverage', {
        status_code: 404,
        request_meta: { record_hidden: 'sample' },
      }),
      requestRow('evt-project', '/api/genes/{symbol}/profile', {
        status_code: 404,
        request_meta: { record_hidden: 'project' },
      }),
      requestRow('evt-odd', '/api/families/{family_id}/members', {
        status_code: 404,
        request_meta: { record_hidden: true, _escaped: 'path' },
      }),
    ]);
    renderPage();
    await screen.findByText('/api/families/{family_id}/members');

    expect(flagsOf('/api/samples/{sample_id}/coverage')).toHaveTextContent(
      "Sample outside the user's projects",
    );
    expect(flagsOf('/api/genes/{symbol}/profile')).toHaveTextContent(
      "Project outside the user's projects",
    );
    const odd = flagsOf('/api/families/{family_id}/members');
    expect(within(odd).getByText("Record outside the user's projects")).toBeInTheDocument();
    expect(within(odd).getByText('Escaped')).toBeInTheDocument();
  });

  // An account made at a first Microsoft sign-in, an administrator's role or activation
  // change (request_meta.account_change, each field from and to), and every request of the
  // emergency administrator's password sign-in (request_meta.emergency_sign_in).
  it('flags an account made or changed, and the emergency sign-in', async () => {
    serve([
      requestRow('evt-made', '/api/auth/me', {
        request_meta: {
          account_change: {
            user_id: 'u-new',
            email: 'new.colleague@example.org',
            created: 'microsoft_sign_in',
            role: { from: null, to: 'viewer' },
          },
        },
      }),
      requestRow('evt-role', '/api/auth/users/{user_id}', {
        method: 'PATCH',
        request_meta: {
          emergency_sign_in: true,
          account_change: {
            user_id: 'u-new',
            email: 'new.colleague@example.org',
            role: { from: 'viewer', to: 'admin' },
            is_active: { from: true, to: false },
          },
        },
      }),
      requestRow('evt-emergency', '/api/auth/login', {
        method: 'POST',
        request_meta: { emergency_sign_in: true },
      }),
      requestRow('evt-not-flags', '/api/projects', {
        request_meta: { emergency_sign_in: 'yes', account_change: { user_id: 'u-new' } },
      }),
    ]);
    renderPage();
    await screen.findByText('/api/projects');

    expect(flagsOf('/api/auth/me')).toHaveTextContent(
      'Account new.colleague@example.org: made at its first Microsoft sign-in',
    );
    const role = flagsOf('/api/auth/users/{user_id}');
    expect(within(role).getByText('Emergency sign-in')).toHaveAttribute(
      'title',
      expect.stringMatching(/emergency administrator’s password beside Microsoft sign-in/),
    );
    expect(
      within(role).getByText('Account new.colleague@example.org: role viewer → admin, deactivated'),
    ).toBeInTheDocument();
    expect(flagsOf('/api/auth/login')).toHaveTextContent(/^Emergency sign-in$/);
    // A value that is not the writer's own (a string, a change without any field) is no flag.
    expect(flagsOf('/api/projects')).toHaveTextContent(/^—$/);
  });

  // An account's projects or its levels in them, set on the Users page.
  it.each([
    ['its projects', { project_ids: { from: [], to: ['p1'] } }],
    ['its levels', { reader_project_ids: { from: ['p1'], to: [] } }],
  ])('flags a change of an account’s project access: %s', async (_case, fields) => {
    serve([
      requestRow('evt-access', '/api/auth/users/{user_id}', {
        method: 'PATCH',
        request_meta: {
          account_change: { user_id: 'u-new', email: 'new.colleague@example.org', ...fields },
        },
      }),
    ]);
    renderPage();
    await screen.findByText('/api/auth/users/{user_id}');

    expect(flagsOf('/api/auth/users/{user_id}')).toHaveTextContent(
      'Account new.colleague@example.org: project access changed',
    );
  });

  it('flags a UI interaction stored with escaped values, and nothing a client could write itself', async () => {
    serve(
      [],
      [
        uiEventRow('ui-escaped', 'Open family', {
          _escaped: ['label', 'to_path'],
          // A client writes detail: only the writer's _escaped is a flag there.
          record_hidden: 'family',
          note: 'x-synthetic-detail',
        }),
        uiEventRow('ui-clean', 'Save note', {}),
      ],
    );
    renderPage();
    fireEvent.click(await screen.findByRole('button', { name: 'UI interactions' }));
    await screen.findByText('Save note');

    expect(flagsOf('Open family')).toHaveTextContent(/^Escaped: label, to path$/);
    expect(flagsOf('Save note')).toHaveTextContent(/^—$/);
    expect(screen.queryByText(/outside the user's projects/)).not.toBeInTheDocument();
    expect(screen.queryByText(/x-synthetic/)).not.toBeInTheDocument();
  });
});
