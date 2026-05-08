import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import Dashboard from '../Dashboard';
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
              species_name: 'Human',
              assembly_name: 'GRCh38',
              assembly_version: 'v1',
              families: [
                {
                  family_id: 'F1',
                  members: [
                    { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
                    { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
                  ],
                },
              ],
              samples: ['S1'],
            },
          ],
        });
      }
      if (url === '/families') {
        return Promise.resolve({
          data: [
            {
              family_id: 'F1',
              members: [
                { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
                { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
              ],
            },
            {
              family_id: 'F2',
              members: [
                { sample_id: 'S3', role: 'proband', affected: false, sex: 'female' },
                { sample_id: 'S4', role: 'sibling', affected: false, sex: 'male' },
              ],
            },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
  },
}));

const renderDashboard = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('Dashboard admin section', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows data management link for admin users', () => {
    localStorage.setItem('role', 'admin');
    renderDashboard();
    const link = screen.getByRole('link', { name: /data management/i });
    expect(link).toHaveAttribute('href', '/admin/data');
    expect(screen.getByRole('link', { name: /gene reference sync/i })).toHaveAttribute(
      'href',
      '/admin/gene-reference',
    );
    expect(screen.getByRole('link', { name: /organisms & assemblies/i })).toHaveAttribute(
      'href',
      '/reference-data',
    );
    expect(screen.getByRole('link', { name: /gene panels/i })).toHaveAttribute('href', '/panels');
    expect(screen.getByRole('link', { name: /upload sample data/i })).toHaveAttribute(
      'href',
      '/upload-data',
    );
    expect(screen.queryByRole('link', { name: /^tags$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /preset filters/i })).not.toBeInTheDocument();
  });

  it('hides admin section for non-admin users', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    expect(screen.queryByText('Admin')).not.toBeInTheDocument();
  });

  it('shows documentation link', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    const link = screen.getByRole('link', { name: /user guide/i });
    expect(link).toHaveAttribute('href', '/docs');
  });

  it('keeps the new-features and issue-request links out of the dashboard toolbar', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    expect(screen.queryByRole('link', { name: /new features/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /issues & requests/i })).not.toBeInTheDocument();
  });

  it('keeps reference setup links out of the general viewer dashboard', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    expect(screen.queryByRole('link', { name: /organisms/i })).not.toBeInTheDocument();
  });

  it('shows the panel catalog to non-admin users', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    expect(screen.getByRole('link', { name: /panel catalog/i })).toHaveAttribute('href', '/panels');
  });

  it('shows gene explorer navigation', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();
    const link = screen.getByRole('link', { name: /gene explorer/i });
    expect(link).toHaveAttribute('href', '/genes');
  });

  it('links to the family intake workspace instead of embedding the form', () => {
    localStorage.setItem('role', 'admin');
    renderDashboard();
    expect(screen.queryByRole('heading', { name: /family builder/i })).not.toBeInTheDocument();
  });

  it('keeps the top workspace card focused on the catalog only', () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    expect(screen.queryByRole('heading', { name: /family workflow/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /browse families/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /^import family$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /upload sample data/i })).not.toBeInTheDocument();
  });

  it('shows the project catalog overview directly on the dashboard', async () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument(),
    );
    const catalogHeading = screen.getByRole('heading', { name: /project catalog/i });
    const projectButton = screen.getByRole('button', { name: /project one/i });

    expect(screen.getByText(/projects 1/i)).toBeInTheDocument();
    expect(screen.getByText(/families 2/i)).toBeInTheDocument();
    expect(screen.getByText(/samples 4/i)).toBeInTheDocument();
    expect(catalogHeading).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /open full catalog/i })).not.toBeInTheDocument();
    expect(projectButton.closest('section')).toBe(catalogHeading.closest('section'));
  });

  it('searches the dashboard catalog by sample id', async () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole('textbox', { name: /search projects, families, or samples/i }), {
      target: { value: 'S3' },
    });

    await waitFor(() => expect(screen.getByText(/projects 0/i)).toBeInTheDocument());
    expect(screen.getByText(/families 1/i)).toBeInTheDocument();
    expect(screen.getByText(/samples 2/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /project one/i })).not.toBeInTheDocument();
    expect(screen.getByTestId('unassigned-families')).toHaveTextContent('F2');
  });

  it('shows the linked family when searching by a project sample id', async () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole('textbox', { name: /search projects, families, or samples/i }), {
      target: { value: 'S1' },
    });

    await waitFor(() => expect(screen.getByRole('link', { name: 'F1' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument();
  });

  it('shows the linked family when searching by family id', async () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    await waitFor(() =>
      expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument(),
    );

    fireEvent.change(screen.getByRole('textbox', { name: /search projects, families, or samples/i }), {
      target: { value: 'F1' },
    });

    await waitFor(() => expect(screen.getByRole('link', { name: 'F1' })).toBeInTheDocument());
    expect(screen.getByRole('button', { name: /project one/i })).toBeInTheDocument();
  });

  it('uses a consistent nested family table column layout for project details', async () => {
    localStorage.setItem('role', 'viewer');
    renderDashboard();

    const projectButton = await screen.findByRole('button', { name: /project one/i });
    fireEvent.click(projectButton);

    const familyLink = await screen.findByRole('link', { name: 'F1' });
    const nestedTable = familyLink.closest('table');

    expect(nestedTable).not.toBeNull();
    expect(nestedTable?.querySelectorAll('colgroup col')).toHaveLength(2);
    expect(nestedTable?.querySelector('.family-catalog-family-column')).not.toBeNull();
    expect(familyLink.closest('td')).toHaveClass('family-catalog-family-cell');
  });
});
