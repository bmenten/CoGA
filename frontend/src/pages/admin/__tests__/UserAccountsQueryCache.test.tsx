// The Users, Projects and Audit logs pages read the account list (GET /auth/users) under one
// cache key. The audit page used to cache only each account's id and e-mail there, and the page
// opened next within the app's stale time read those as full accounts: Users threw on
// `u.projects.length`, and Projects lost every user's name (UI-1). Each test opens Audit logs
// and then another page with one query client, as the app does.
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import type React from 'react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../../../lib/api';
import { APP_QUERY_STALE_TIME_MS } from '../../../lib/queryClient';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import ProjectsPage from '../../projects/ProjectsPage';
import AdminAuditLogsPage from '../AdminAuditLogsPage';
import UserListPage from '../UserListPage';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), post: vi.fn(), put: vi.fn(), patch: vi.fn(), delete: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);

const ACCOUNTS = [
  {
    id: 'u1',
    username: 'ann',
    email: 'ann@example.com',
    first_name: 'Ann',
    last_name: 'Lee',
    affiliation: 'Lab',
    is_active: true,
    role: 'admin',
    projects: ['p1'],
    created_at: '2026-01-05T09:00:00Z',
  },
  {
    id: 'u2',
    username: 'ben',
    email: 'ben@example.com',
    first_name: 'Ben',
    last_name: 'Ray',
    affiliation: null,
    is_active: false,
    role: 'viewer',
    projects: [],
    created_at: '2026-02-11T09:00:00Z',
  },
];

const PROJECTS = [
  {
    id: 'p1',
    name: 'Rare disease',
    description: 'Synthetic trios',
    user_ids: ['u1'],
    families: [],
    samples: [],
  },
];

const EMPTY_AUDIT_PAGE = { page: 1, page_size: 50, total: 0, items: [] };

// The app keeps a result fresh for 30 seconds: a page opened within that time reads the list
// from the cache instead of asking for it again.
const appQueryClient = () =>
  createTestQueryClient({ defaultOptions: { queries: { staleTime: APP_QUERY_STALE_TIME_MS } } });

const renderPage = (queryClient: QueryClient, page: React.ReactElement) =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>{page}</MemoryRouter>
    </QueryClientProvider>,
  );

// Open Audit logs until its user filter lists the accounts, then leave it.
const openAuditLogs = async (queryClient: QueryClient) => {
  const view = renderPage(queryClient, <AdminAuditLogsPage />);
  const userFilter = await screen.findByRole('combobox', { name: 'Filter user' });
  await within(userFilter).findByRole('option', { name: 'ben@example.com' });
  view.unmount();
};

const accountListRequests = () =>
  mockedGet.mock.calls.filter(([url]) => url === '/auth/users').length;

const rowOf = (email: string): HTMLElement => {
  const row = screen.getByRole('cell', { name: email }).closest('tr');
  if (!row) throw new Error(`no row for ${email}`);
  return row;
};

describe('the account list the admin pages share', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedGet.mockImplementation((url: string) => {
      if (url === '/auth/users') return Promise.resolve({ data: ACCOUNTS });
      if (url === '/projects') return Promise.resolve({ data: PROJECTS });
      if (url === '/admin/audit-logs') return Promise.resolve({ data: EMPTY_AUDIT_PAGE });
      if (url === '/species' || url === '/assemblies') return Promise.resolve({ data: [] });
      return Promise.reject(new Error(`unexpected GET ${url}`));
    });
  });

  it('Users, opened right after Audit logs, shows each account’s role, activation and project access', async () => {
    const queryClient = appQueryClient();
    await openAuditLogs(queryClient);

    renderPage(queryClient, <UserListPage />);
    await screen.findByRole('table');

    const ann = rowOf('ann@example.com');
    expect(within(ann).getByText('Ann Lee')).toBeInTheDocument();
    expect(within(ann).getByText('admin')).toBeInTheDocument();
    expect(within(ann).getByRole('checkbox')).toBeChecked();
    expect(within(ann).getByText('Rare disease')).toBeInTheDocument();

    const ben = rowOf('ben@example.com');
    expect(within(ben).getByText('viewer')).toBeInTheDocument();
    expect(within(ben).getByRole('checkbox')).not.toBeChecked();
    expect(within(ben).getByText('No project access')).toBeInTheDocument();

    // One request served both pages.
    expect(accountListRequests()).toBe(1);
  });

  it('Projects, opened right after Audit logs, names each user it can give access', async () => {
    const queryClient = appQueryClient();
    await openAuditLogs(queryClient);

    renderPage(queryClient, <ProjectsPage />);

    expect(await screen.findByText('Ann Lee · ann@example.com')).toBeInTheDocument();
    expect(screen.getByText('Ben Ray · ben@example.com')).toBeInTheDocument();
    expect(accountListRequests()).toBe(1);
  });
});
