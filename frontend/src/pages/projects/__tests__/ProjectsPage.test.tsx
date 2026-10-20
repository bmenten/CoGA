import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, it, vi, type Mock } from 'vitest';
import ProjectsPage from '../ProjectsPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              id: 'p1',
              name: 'Project One',
              description: 'Pilot oncology cohort',
              species_id: 'sp1',
              assembly_id: 'asm1',
              user_ids: ['u1'],
              reader_user_ids: [],
              families: [
                {
                  family_id: 'F2',
                  created_at: '2026-05-10T12:00:00Z',
                  members: [{ sample_id: 'S3' }],
                },
                {
                  family_id: 'F1',
                  created_at: '2026-04-01T12:00:00Z',
                  members: [{ sample_id: 'S1' }, { sample_id: 'S2' }],
                },
              ],
              samples: ['S1'],
            },
          ],
        });
      }
      if (url === '/species') {
        return Promise.resolve({
          data: [{ _id: 'sp1', name: 'Homo sapiens', common_name: 'human' }],
        });
      }
      if (url === '/assemblies') {
        return Promise.resolve({
          data: [{ _id: 'asm1', assembly_name: 'GRCh38', version: 'p14' }],
        });
      }
      if (url === '/auth/users') {
        return Promise.resolve({
          data: [
            {
              id: 'u1',
              email: 'viewer@example.com',
              first_name: 'View',
              last_name: 'Er',
              role: 'viewer',
              is_active: true,
              projects: ['p1'],
            },
            {
              id: 'u2',
              email: 'student@example.com',
              first_name: 'Stu',
              last_name: 'Dent',
              role: 'viewer',
              is_active: true,
              projects: [],
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    post: vi.fn(),
    put: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('ProjectsPage', () => {
  beforeEach(() => {
    localStorage.setItem('role', 'admin');
  });

  it('shows a searchable project catalog and editable project settings', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/projects']}>
          <Routes>
            <Route path="/projects" element={<ProjectsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Project One/)).toBeInTheDocument());
    expect(screen.getByLabelText(/Search projects/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /create new project/i })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByText(/2 families · 3 samples/)).toBeInTheDocument());
    expect(screen.getAllByText(/Homo sapiens/i).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/GRCh38 p14/i).length).toBeGreaterThan(0);
    await waitFor(() => expect(screen.getAllByDisplayValue('Project One').length).toBeGreaterThan(0));
    await waitFor(() =>
      expect(screen.getAllByDisplayValue('Pilot oncology cohort').length).toBeGreaterThan(0),
    );
    expect(screen.getByText(/View Er · viewer@example.com/)).toBeInTheDocument();
    expect(screen.getAllByText('F1').length).toBeGreaterThan(0);
    expect(screen.getByText('S1')).toBeInTheDocument();
  });

  // #610 — a failed list read "No projects yet", with counts of 0.
  it('says the projects could not be loaded, not that there are none, and retries', async () => {
    const get = api.get as unknown as Mock;
    const working = get.getMockImplementation()!;
    let failing = true;
    get.mockImplementation((url: string, config?: unknown) =>
      failing && url === '/projects'
        ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
        : working(url, config),
    );
    try {
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/projects']}>
            <Routes>
              <Route path="/projects" element={<ProjectsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      expect(
        await screen.findByText(/Could not load the projects — this is not an empty result/),
      ).toBeInTheDocument();
      expect(screen.queryByText('No projects yet')).not.toBeInTheDocument();
      expect(screen.queryByText(/Showing 0 of 0 projects/)).not.toBeInTheDocument();
      expect(screen.getByText('Projects —')).toBeInTheDocument();

      failing = false;
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('Projects 1')).toBeInTheDocument();
    } finally {
      get.mockImplementation(working);
    }
  });

  // SEC-1: each member is an analyst (read and change) or a reader (read only); a member added
  // starts as an analyst, and the save sends the readers among the members.
  it('sets each member’s access level and saves the readers with the members', async () => {
    vi.mocked(api.put).mockResolvedValue({ data: {} });
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/projects']}>
          <Routes>
            <Route path="/projects" element={<ProjectsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const level = await screen.findByRole('combobox', { name: 'Access level of viewer@example.com' });
    expect(level).toHaveValue('analyst');
    // No level for someone who is no member.
    expect(screen.queryByRole('combobox', { name: 'Access level of student@example.com' })).not.toBeInTheDocument();

    const userAccess = screen.getByRole('heading', { name: 'User access' }).closest('.surface-card-muted') as HTMLElement;
    fireEvent.click(within(userAccess).getByRole('checkbox', { name: /Stu Dent/ }));
    const studentLevel = screen.getByRole('combobox', { name: 'Access level of student@example.com' });
    expect(studentLevel).toHaveValue('analyst');
    fireEvent.change(studentLevel, { target: { value: 'reader' } });
    fireEvent.click(screen.getByRole('button', { name: 'Save settings' }));

    await waitFor(() => expect(api.put).toHaveBeenCalledTimes(1));
    expect(vi.mocked(api.put).mock.calls[0][1]).toMatchObject({
      user_ids: ['u1', 'u2'],
      reader_user_ids: ['u2'],
    });
  });

  it('counts a project’s readers among its users', async () => {
    const get = api.get as unknown as Mock;
    const working = get.getMockImplementation()!;
    get.mockImplementation((url: string, config?: unknown) =>
      url === '/projects'
        ? working(url, config).then((response: { data: Array<Record<string, unknown>> }) => ({
            data: response.data.map((project) => ({ ...project, user_ids: ['u1', 'u2'], reader_user_ids: ['u2'] })),
          }))
        : working(url, config),
    );
    try {
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/projects']}>
            <Routes>
              <Route path="/projects" element={<ProjectsPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      expect(await screen.findByText('2 (1 reader)')).toBeInTheDocument();
      expect(await screen.findByRole('combobox', { name: 'Access level of student@example.com' })).toHaveValue('reader');
    } finally {
      get.mockImplementation(working);
    }
  });

  it('sorts linked families by upload date newest first by default', async () => {
    const queryClient = createTestQueryClient();
    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/projects']}>
          <Routes>
            <Route path="/projects" element={<ProjectsPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Project One/)).toBeInTheDocument());
    const linkedFamiliesSection = screen
      .getByRole('heading', { name: /linked families/i })
      .closest('.surface-card-muted') as HTMLElement;
    const bodyRows = () => within(linkedFamiliesSection).getAllByRole('row').slice(1);

    await waitFor(() => expect(bodyRows()[0]).toHaveTextContent('F2'));
    expect(bodyRows()[1]).toHaveTextContent('F1');
    expect(bodyRows()[0].querySelector('.project-family-uploaded-cell')).toHaveClass(
      'project-family-uploaded-cell',
    );

    fireEvent.click(within(linkedFamiliesSection).getByText(/Uploaded/));
    await waitFor(() => expect(bodyRows()[0]).toHaveTextContent('F1'));
    expect(bodyRows()[1]).toHaveTextContent('F2');
  });
});
