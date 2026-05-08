import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import FamilyIgvPage from '../FamilyIgvPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const { apiGetMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          projects: ['p1'],
          members: [
            { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
          ],
        },
      });
    }

    if (url === '/projects') {
      return Promise.resolve({
        data: [
          {
            _id: 'p1',
            name: 'Project one',
            species_id: 'sp1',
            assembly_id: 'asm1',
            species_name: 'Homo sapiens',
            assembly_name: 'GRCh38',
            assembly_version: 'p14',
            families: [],
            samples: [],
          },
        ],
      });
    }

    throw new Error(`Unexpected GET ${url}`);
  }),
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: apiGetMock,
  },
}));

vi.mock('../../../components/IgvViewer', () => ({
  default: (props: any) => (
    <div data-testid="igv-viewer">
      {props.genome}|{props.sampleIds.join(',')}|{props.locus ?? ''}
    </div>
  ),
}));

describe('FamilyIgvPage', () => {
  it('uses project reference labels directly from /projects and resolves the IGV genome without extra metadata requests', async () => {
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/igv?locus=chr1:10-20&project_id=p1']}>
          <Routes>
            <Route path="/families/:familyId/igv" element={<FamilyIgvPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/IGV for family F1/i)).toBeInTheDocument());
    await waitFor(() =>
      expect(screen.getByText(/Homo sapiens • GRCh38 p14 • chr1:10-20/i)).toBeInTheDocument(),
    );
    expect(screen.getByTestId('igv-viewer')).toHaveTextContent('hg38|PROBAND,DAD|chr1:10-20');

    const urls = apiGetMock.mock.calls.map(([url]) => String(url));
    expect(urls).toContain('/families/F1');
    expect(urls).toContain('/projects');
    expect(urls.some((url) => url.startsWith('/species'))).toBe(false);
    expect(urls.some((url) => url.startsWith('/assemblies'))).toBe(false);
  });

  it('ignores a project_id that is not linked to the current family', async () => {
    apiGetMock.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            projects: ['p1'],
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
            ],
          },
        });
      }

      if (url === '/projects') {
        return Promise.resolve({
          data: [
            {
              _id: 'p1',
              name: 'Linked project',
              species_id: 'sp1',
              assembly_id: 'asm1',
              species_name: 'Homo sapiens',
              assembly_name: 'GRCh38',
              assembly_version: 'p14',
              families: [],
              samples: [],
            },
            {
              _id: 'p2',
              name: 'Unlinked project',
              species_id: 'sp2',
              assembly_id: 'asm2',
              species_name: 'Mus musculus',
              assembly_name: 'GRCm39',
              assembly_version: 'v1',
              families: [],
              samples: [],
            },
          ],
        });
      }

      throw new Error(`Unexpected GET ${url}`);
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/igv?locus=chr1:10-20&project_id=p2']}>
          <Routes>
            <Route path="/families/:familyId/igv" element={<FamilyIgvPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByText(/Homo sapiens • GRCh38 p14 • chr1:10-20/i)).toBeInTheDocument(),
    );
    expect(screen.queryByText(/Mus musculus • GRCm39 v1/i)).not.toBeInTheDocument();
  });
});
