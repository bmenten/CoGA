import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { beforeEach, describe, it, vi } from 'vitest';
import ProjectsPage from '../ProjectsPage';
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
