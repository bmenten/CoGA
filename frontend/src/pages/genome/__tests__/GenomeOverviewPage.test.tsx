import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { useLocation } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import GenomeOverviewPage from '../GenomeOverviewPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const sidebarSpy = vi.fn();
const workspaceSpy = vi.fn();

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    speciesName: 'Homo sapiens',
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    assemblyId: 'asm1',
    projectId: 'p1',
    isLoading: false,
  }),
}));

vi.mock('../../../lib/useMeasuredWidth', () => ({
  useMeasuredWidth: () => [{ current: null }, 1400] as const,
}));

vi.mock('../GenomeOverviewSidebar', () => ({
  default: (props: any) => {
    sidebarSpy(props);
    const selectedChroms = Object.entries(props.chromSelected)
      .filter(([, enabled]) => Boolean(enabled))
      .map(([chrom]) => chrom)
      .join(',');
    return (
      <div data-testid="genome-sidebar">
        {props.members.map((member: any) => member.sample_id).join(',')}|{selectedChroms}
      </div>
    );
  },
}));

vi.mock('../GenomeOverviewWorkspace', () => ({
  default: (props: any) => {
    workspaceSpy(props);
    return (
      <>
        <div data-testid="genome-workspace">
          {props.visibleMembers.map((member: any) => member.sample_id).join(',')}|{props.visibleRoi?.label || 'no-roi'}
        </div>
        <button type="button" onClick={() => props.navigateToChromosome('X')}>
          Open chromosome
        </button>
        <button
          type="button"
          onClick={() => props.navigateToChromosome('X', { start: 120, end: 180 })}
        >
          Open chromosome region
        </button>
      </>
    );
  },
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-search">{location.pathname}{location.search}</div>;
};

describe('GenomeOverviewPage', () => {
  it('keeps proband-first member order, respects sample and chromosome query state, and forwards ROI', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'SIB', role: 'sibling', affected: false, sex: 'female' },
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
            ],
            projects: ['p1'],
            roi: {
              query: 'GENE-X',
              label: 'GENE-X',
              source: 'gene',
              assembly_id: 'asm1',
              chr: 'X',
              start: 250,
              end: 400,
            },
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        return Promise.resolve({
          data: [
            ...Array.from({ length: 22 }, (_, index) => ({
              chr: String(index + 1),
              size: 1000,
            })),
            { chr: 'X', size: 1000 },
            { chr: 'Y', size: 1000 },
          ],
        });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                segments: true,
                apcad: true,
                haplotypes: true,
                variants: true,
              },
              SIB: {
                coverage: true,
                segments: false,
                apcad: false,
                haplotypes: false,
                variants: false,
              },
              DAD: {
                coverage: false,
                segments: false,
                apcad: false,
                haplotypes: false,
                variants: false,
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter
          initialEntries={['/families/F1/genome?chrom=2&chrom=X&sample=SIB&type=DEL&sample_filter=PROBAND:hom']}
        >
          <LocationProbe />
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
            <Route path="/families/:familyId/chromosome/:chrom" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('genome-sidebar')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toHaveTextContent('SIB|GENE-X'));

    expect(screen.getByTestId('genome-sidebar')).toHaveTextContent('PROBAND,DAD,SIB|2,X');

    const availabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .find((url) => url.startsWith('/families/F1/track-availability?'));
    expect(availabilityCall).toContain('chrom=2');
    expect(availabilityCall).toContain('chrom=X');
    expect(availabilityCall).toContain('type=DEL');
    expect(availabilityCall).toContain('sample_filter=PROBAND%3Ahom');

    const lastWorkspaceProps = workspaceSpy.mock.calls.at(-1)?.[0];
    expect(lastWorkspaceProps.visibleMembers.map((member: any) => member.sample_id)).toEqual([
      'SIB',
    ]);
    expect(lastWorkspaceProps.backDest).toBe(
      '/families/F1/structural-variants?type=DEL&sample_filter=PROBAND%3Ahom&project_id=p1',
    );
  });

  it('navigates to a chromosome region when genome workspace requests a regional jump', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
            ],
            projects: ['p1'],
            roi: null,
          },
        });
      }
      if (url === '/chromosomes/GRCh38') {
        return Promise.resolve({
          data: [
            ...Array.from({ length: 22 }, (_, index) => ({
              chr: String(index + 1),
              size: 1000,
            })),
            { chr: 'X', size: 1000 },
            { chr: 'Y', size: 1000 },
          ],
        });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                segments: true,
                apcad: true,
                haplotypes: true,
                variants: true,
              },
            },
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/genome?chrom=2&project_id=p1&type=DEL']}>
          <Routes>
            <Route path="/families/:familyId/genome" element={<GenomeOverviewPage />} />
            <Route path="/families/:familyId/chromosome/:chrom" element={<LocationProbe />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('genome-workspace')).toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: 'Open chromosome region' }));

    await waitFor(() =>
      expect(screen.getByTestId('location-search')).toHaveTextContent('/families/F1/chromosome/X'),
    );
    expect(screen.getByTestId('location-search')).toHaveTextContent('chrom=2');
    expect(screen.getByTestId('location-search')).toHaveTextContent('project_id=p1');
    expect(screen.getByTestId('location-search')).toHaveTextContent('type=DEL');
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=120');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=180');
  });
});
