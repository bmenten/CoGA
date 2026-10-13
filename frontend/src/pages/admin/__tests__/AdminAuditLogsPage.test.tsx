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

describe('AdminAuditLogsPage', () => {
  it('renders audit log entries and applies method filter', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/auth/users') {
        return Promise.resolve({
          data: [{ id: 'u1', email: 'admin@example.com' }],
        });
      }
      if (url === '/admin/audit-logs') {
        return Promise.resolve({
          data: {
            page: 1,
            page_size: 50,
            total: 1,
            items: [
              {
                id: 'evt-1',
                created_at: '2026-04-28T20:00:00Z',
                user_email: 'admin@example.com',
                method: 'PATCH',
                route_path: '/families/{family_id}/small-variant-tags/{tag_key}',
                path: '/families/F1/small-variant-tags/review',
                status_code: 200,
                duration_ms: 23,
                db_update: {
                  dbEntity: 'families',
                  entityId: 'F1',
                  updateType: 'update',
                },
                error: null,
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: { page: 1, page_size: 50, total: 0, items: [] } });
    });

    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <AdminAuditLogsPage />
        </MemoryRouter>
      </QueryClientProvider>
    );

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
});

// AUDIT-4: what the writers flag in a row, shown on the page. A request answered as for an
// unknown record carries request_meta.record_hidden (#785); a row in which a value the database
// cannot store was escaped names its columns in request_meta._escaped, or for a UI event in
// detail._escaped (#768). The page shows these flags and nothing else of request_meta or detail.
describe('AdminAuditLogsPage flags', () => {
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

  const uiEventRow = (id: string, label: string, detail: Record<string, unknown>) => ({
    id,
    created_at: '2026-04-28T20:00:00Z',
    user_email: 'viewer@example.com',
    event_type: 'click',
    category: 'button',
    label,
    path: '/families',
    detail,
  });

  const page = (items: unknown[]) => ({ page: 1, page_size: 50, total: items.length, items });

  const serve = (requests: unknown[], uiEvents: unknown[] = []) => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/auth/users') return Promise.resolve({ data: [] });
      if (url === '/admin/audit-logs') return Promise.resolve({ data: page(requests) });
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
