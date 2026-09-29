// Pins the admin Users page: it reads /auth/users and /projects, lists each account with its
// project names, and its only write — the activation checkbox — PATCHes nothing but is_active.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import UserListPage from '../UserListPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), patch: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);
const mockedPatch = vi.mocked(api.patch);

type TestUser = {
  id: string;
  email: string;
  first_name?: string;
  last_name?: string;
  affiliation?: string;
  role: string;
  is_active: boolean;
  projects: string[];
};

const ANN: TestUser = {
  id: 'u1',
  email: 'ann@example.com',
  first_name: 'Ann',
  last_name: 'Lee',
  affiliation: 'CMGG',
  role: 'admin',
  is_active: true,
  projects: ['p1', 'p2'],
};
// A fresh self-registration: inactive until an administrator approves it.
const PENDING: TestUser = {
  id: 'u2',
  email: 'new.registrant@example.com',
  role: 'viewer',
  is_active: false,
  projects: [],
};
const SURNAME_ONLY: TestUser = {
  id: 'u3',
  email: 'peeters@example.com',
  last_name: 'Peeters',
  affiliation: '',
  role: 'viewer',
  is_active: true,
  projects: ['p3'],
};
const USERS = [ANN, PENDING, SURNAME_ONLY];

const PROJECTS = [
  { id: 'p1', name: 'Rare disease', description: 'Trio exomes' },
  { id: 'p2', name: 'NIPT' },
  { id: 'p3', name: 'Oncology' },
];

// Serve the two lists the page reads; any other GET is a request it should not make.
const serve = (users: TestUser[]) => {
  mockedGet.mockImplementation((url: string) => {
    if (url === '/auth/users') return Promise.resolve({ data: users });
    if (url === '/projects') return Promise.resolve({ data: PROJECTS });
    return Promise.reject(new Error(`unexpected GET ${url}`));
  });
};

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <UserListPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
};

const rowOf = (email: string): HTMLElement => {
  const row = screen.getByRole('cell', { name: email }).closest('tr');
  if (!row) throw new Error(`no row for ${email}`);
  return row;
};

describe('UserListPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPatch.mockReset();
    serve(USERS);
  });

  it('asks for the accounts and the project catalogue, and draws nothing until both are in', async () => {
    let releaseProjects: (value: unknown) => void = () => undefined;
    mockedGet.mockImplementation((url: string) =>
      url === '/auth/users'
        ? Promise.resolve({ data: USERS })
        : new Promise((resolve) => {
            releaseProjects = resolve;
          }),
    );
    const queryClient = renderPage();

    expect(screen.getByRole('heading', { name: 'Loading users' })).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith('/auth/users');
    expect(mockedGet).toHaveBeenCalledWith('/projects');

    // The accounts alone are not enough: every project-access cell would read as empty.
    await waitFor(() => expect(queryClient.getQueryData(['admin', 'users'])).toEqual(USERS));
    expect(screen.getByRole('heading', { name: 'Loading users' })).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();

    releaseProjects({ data: PROJECTS });
    expect(await screen.findByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Users' })).toBeInTheDocument();
  });

  it('lists every account with its name, affiliation, role and the projects it can access', async () => {
    renderPage();
    await screen.findByRole('table');

    // One header row plus one row per account.
    expect(screen.getAllByRole('row')).toHaveLength(USERS.length + 1);

    const ann = rowOf('ann@example.com');
    expect(within(ann).getByText('Ann Lee')).toBeInTheDocument();
    expect(within(ann).getByText('CMGG')).toBeInTheDocument();
    expect(within(ann).getByText('admin')).toBeInTheDocument();
    // Project ids are resolved to names, and a project the account is not on is not listed.
    expect(within(ann).getByText('Rare disease')).toBeInTheDocument();
    expect(within(ann).getByText('NIPT')).toBeInTheDocument();
    expect(within(ann).queryByText('Oncology')).not.toBeInTheDocument();
  });

  it('shows a dash for a missing name or affiliation, and says when an account has no project access', async () => {
    renderPage();
    await screen.findByRole('table');

    const pending = rowOf('new.registrant@example.com');
    expect(within(pending).getAllByText('—')).toHaveLength(2);
    expect(within(pending).getByText('No project access')).toBeInTheDocument();

    // A lone surname is shown as is (no stray separator); an empty affiliation is a dash.
    const surnameOnly = rowOf('peeters@example.com');
    expect(within(surnameOnly).getAllByRole('cell')[1].textContent).toBe('Peeters');
    expect(within(surnameOnly).getByText('—')).toBeInTheDocument();
    expect(within(surnameOnly).getByText('Oncology')).toBeInTheDocument();
  });

  it('ticks the activation checkbox of active accounts only', async () => {
    renderPage();
    await screen.findByRole('table');

    expect(within(rowOf('ann@example.com')).getByRole('checkbox')).toBeChecked();
    expect(within(rowOf('new.registrant@example.com')).getByRole('checkbox')).not.toBeChecked();
  });

  it('deactivating an account PATCHes only is_active=false for it, then shows the reloaded state', async () => {
    const user = userEvent.setup();
    mockedPatch.mockResolvedValue({ data: { ...ANN, is_active: false } });
    renderPage();
    await screen.findByRole('table');

    // What the server holds after the change; the page must show that, not a local guess.
    serve([{ ...ANN, is_active: false }, PENDING, SURNAME_ONLY]);
    await user.click(within(rowOf('ann@example.com')).getByRole('checkbox'));

    await waitFor(() =>
      expect(within(rowOf('ann@example.com')).getByRole('checkbox')).not.toBeChecked(),
    );
    expect(mockedPatch).toHaveBeenCalledTimes(1);
    const [url, body] = mockedPatch.mock.calls[0];
    expect(url).toBe('/auth/users/u1');
    // Project access is managed from project settings (the API refuses it here), so the body
    // carries the activation flag and nothing else.
    expect(body).toStrictEqual({ is_active: false });
    expect(mockedGet.mock.calls.filter(([requested]) => requested === '/auth/users')).toHaveLength(2);
  });

  it('activating a pending registration PATCHes is_active=true', async () => {
    const user = userEvent.setup();
    mockedPatch.mockResolvedValue({ data: { ...PENDING, is_active: true } });
    renderPage();
    await screen.findByRole('table');

    await user.click(within(rowOf('new.registrant@example.com')).getByRole('checkbox'));

    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith('/auth/users/u2', { is_active: true }),
    );
  });

  it('sends the account id as one encoded path segment', async () => {
    const user = userEvent.setup();
    serve([{ ...PENDING, id: '../projects/p1' }]);
    mockedPatch.mockResolvedValue({ data: {} });
    renderPage();
    await screen.findByRole('table');

    await user.click(within(rowOf('new.registrant@example.com')).getByRole('checkbox'));

    // An id must not be able to steer the PATCH at another endpoint (#521).
    await waitFor(() =>
      expect(mockedPatch).toHaveBeenCalledWith('/auth/users/..%2Fprojects%2Fp1', {
        is_active: true,
      }),
    );
  });

  it('locks an account’s checkbox while its change is in flight, leaving the other accounts editable', async () => {
    const user = userEvent.setup();
    let finishPatch: (value: unknown) => void = () => undefined;
    mockedPatch.mockImplementation(
      () =>
        new Promise((resolve) => {
          finishPatch = resolve;
        }),
    );
    renderPage();
    await screen.findByRole('table');

    const annBox = within(rowOf('ann@example.com')).getByRole('checkbox');
    await user.click(annBox);

    await waitFor(() => expect(mockedPatch).toHaveBeenCalledTimes(1));
    expect(annBox).toBeDisabled();
    expect(within(rowOf('new.registrant@example.com')).getByRole('checkbox')).toBeEnabled();
    // A second click cannot send a second, contradictory PATCH for the same account.
    await user.click(annBox);
    expect(mockedPatch).toHaveBeenCalledTimes(1);

    finishPatch({ data: { ...ANN, is_active: false } });
    await waitFor(() => expect(annBox).toBeEnabled());
  });

  it('keeps showing the stored state when an activation change fails', async () => {
    const user = userEvent.setup();
    mockedPatch.mockRejectedValue({
      response: { status: 500, data: { detail: 'Database unavailable' } },
    });
    const queryClient = renderPage();
    await screen.findByRole('table');

    const annBox = within(rowOf('ann@example.com')).getByRole('checkbox');
    await user.click(annBox);

    await waitFor(() =>
      expect(queryClient.getMutationCache().getAll()[0]?.state.status).toBe('error'),
    );
    // Nothing changed on the server, so the account must still read as active — and the
    // checkbox is usable again for a retry.
    expect(annBox).toBeChecked();
    expect(annBox).toBeEnabled();
  });

  it('has no role or project-access editor: the activation checkbox is the only control', async () => {
    renderPage();
    await screen.findByRole('table');

    expect(screen.getAllByRole('checkbox')).toHaveLength(USERS.length);
    expect(screen.queryByRole('combobox')).not.toBeInTheDocument();
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
    expect(
      screen.getByText(/Project access is managed from\s+the project settings view/),
    ).toBeInTheDocument();
  });

  it.each([
    ['the API’s detail', { response: { status: 403, data: { detail: 'Not authorized' } } }, 'Not authorized'],
    ['the error message when there is no detail', new Error('Request timed out'), 'Request timed out'],
    ['a generic sentence for an empty error', {}, 'The user list could not be loaded.'],
    ['a generic sentence for a non-object failure', 'boom', 'The user list could not be loaded.'],
  ])('says the users could not be loaded, giving %s', async (_case, failure, message) => {
    mockedGet.mockImplementation((url: string) =>
      url === '/auth/users' ? Promise.reject(failure) : Promise.resolve({ data: PROJECTS }),
    );
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Could not load users' })).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });

  it('refuses to draw the table when only the project catalogue fails', async () => {
    mockedGet.mockImplementation((url: string) =>
      url === '/projects'
        ? Promise.reject({ response: { status: 500, data: { detail: 'Projects unavailable' } } })
        : Promise.resolve({ data: USERS }),
    );
    renderPage();

    expect(await screen.findByRole('heading', { name: 'Could not load users' })).toBeInTheDocument();
    expect(screen.getByText('Projects unavailable')).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
  });
});
