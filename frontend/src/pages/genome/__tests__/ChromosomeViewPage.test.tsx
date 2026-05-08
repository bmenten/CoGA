import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { describe, expect, it, vi, type Mock } from 'vitest';
import ChromosomeViewPage from '../ChromosomeViewPage';
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

vi.mock('../ChromosomeViewSidebar', () => ({
  default: (props: any) => {
    sidebarSpy(props);
    return (
      <div data-testid="chromosome-sidebar">
        {props.members.map((member: any) => member.sample_id).join(',')}
      </div>
    );
  },
}));

vi.mock('../ChromosomeViewWorkspace', () => ({
  default: (props: any) => {
    workspaceSpy(props);
    return (
      <>
        <div data-testid="chromosome-workspace">
          {props.region.start}:{props.region.end}|{props.visibleMembers.map((member: any) => member.sample_id).join(',')}|
          {props.visibleRoi?.label || 'no-roi'}
        </div>
        <button type="button" onClick={() => props.onRegionSelect(120, 180)}>
          Select region
        </button>
      </>
    );
  },
}));

const LocationProbe = () => {
  const location = useLocation();
  return <div data-testid="location-search">{location.pathname}{location.search}</div>;
};

describe('ChromosomeViewPage', () => {
  it('sorts members proband-first, keeps query region, and forwards ROI plus sample filters', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
            ],
            projects: ['p1'],
            roi: {
              query: 'BRCA1',
              label: 'BRCA1',
              source: 'gene',
              assembly_id: 'asm1',
              chr: '1',
              start: 140,
              end: 180,
            },
          },
        });
      }
      if (url === '/chromosomes/GRCh38/1') {
        return Promise.resolve({ data: { chr: '1', size: 1000 } });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                apcad: true,
                variants: true,
                small_variants: true,
                haplotypes: true,
              },
              MOTHER: {
                coverage: false,
                apcad: false,
                variants: false,
                small_variants: false,
                haplotypes: false,
              },
              FATHER: {
                coverage: true,
                apcad: false,
                variants: true,
                small_variants: false,
                haplotypes: false,
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
          initialEntries={['/families/F1/chromosome/1?start=100&end=200&origin=small&project_id=p1&sample_filter=PROBAND:het']}
        >
          <LocationProbe />
          <Routes>
            <Route path="/families/:familyId/chromosome/:chrom" element={<ChromosomeViewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByTestId('chromosome-sidebar')).toBeInTheDocument());
    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('100:200'));

    expect(screen.getByTestId('chromosome-sidebar')).toHaveTextContent('PROBAND,FATHER,MOTHER');
    expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('BRCA1');
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=100');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=200');

    const availabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .find((url) => url.startsWith('/families/F1/track-availability?'));
    expect(availabilityCall).toContain('chrom=1');
    expect(availabilityCall).toContain('start=100');
    expect(availabilityCall).toContain('end=200');
    expect(availabilityCall).toContain('sample_filter=PROBAND%3Ahet');

    const lastWorkspaceProps = workspaceSpy.mock.calls.at(-1)?.[0];
    expect(lastWorkspaceProps.visibleMembers.map((member: any) => member.sample_id)).toEqual([
      'PROBAND',
      'FATHER',
      'MOTHER',
    ]);
    expect(lastWorkspaceProps.backDest).toBe('/families/F1/small-variants?origin=small&sample_filter=PROBAND%3Ahet&project_id=p1');

    fireEvent.click(screen.getByRole('button', { name: 'Select region' }));

    await waitFor(() => expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('120:180'));
    expect(screen.getByTestId('location-search')).toHaveTextContent('start=120');
    expect(screen.getByTestId('location-search')).toHaveTextContent('end=180');

    const latestAvailabilityCall = (api.get as unknown as Mock).mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.startsWith('/families/F1/track-availability?'))
      .at(-1);
    expect(latestAvailabilityCall).toContain('start=120');
    expect(latestAvailabilityCall).toContain('end=180');
  });

  it('honors repeated sample params when initializing visible members', async () => {
    (api.get as unknown as Mock).mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
            ],
            projects: ['p1'],
            roi: null,
          },
        });
      }
      if (url === '/chromosomes/GRCh38/1') {
        return Promise.resolve({ data: { chr: '1', size: 1000 } });
      }
      if (url.startsWith('/families/F1/track-availability?')) {
        return Promise.resolve({
          data: {
            samples: {
              PROBAND: {
                coverage: true,
                apcad: true,
                variants: true,
                small_variants: true,
                haplotypes: true,
              },
              MOTHER: {
                coverage: true,
                apcad: false,
                variants: false,
                small_variants: false,
                haplotypes: false,
              },
              FATHER: {
                coverage: true,
                apcad: false,
                variants: true,
                small_variants: false,
                haplotypes: false,
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
          initialEntries={['/families/F1/chromosome/1?sample=PROBAND&sample=FATHER&project_id=p1']}
        >
          <LocationProbe />
          <Routes>
            <Route path="/families/:familyId/chromosome/:chrom" element={<ChromosomeViewPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() =>
      expect(screen.getByTestId('chromosome-workspace')).toHaveTextContent('0:1000|PROBAND,FATHER|'),
    );

    const lastWorkspaceProps = workspaceSpy.mock.calls.at(-1)?.[0];
    expect(lastWorkspaceProps.visibleMembers.map((member: any) => member.sample_id)).toEqual([
      'PROBAND',
      'FATHER',
    ]);
    expect(screen.getByTestId('location-search')).toHaveTextContent('sample=PROBAND');
    expect(screen.getByTestId('location-search')).toHaveTextContent('sample=FATHER');
  });
});
