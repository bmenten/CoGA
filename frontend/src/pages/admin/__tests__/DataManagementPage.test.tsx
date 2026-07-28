import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import DataManagementPage from '../DataManagementPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

// The embedded Family Workspace is exercised by its own test suite; stub it here
// so this page test does not depend on the full family-detail query graph.
vi.mock('../../families/FamilyDetailPage', () => ({
  default: ({ familyId }: { familyId?: string }) => (
    <div data-testid="embedded-family-detail">Workspace {familyId}</div>
  ),
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    delete: vi.fn(),
    put: vi.fn(),
    post: vi.fn(),
  },
}));

const familyDetail = {
  family_id: 'F1',
  metadata: { demo: true },
  projects: ['P1'],
  sample_count: 2,
  track_counts: {
    small_variants: 5000,
    structural_variants: 1200,
    repeat_expansions: 0,
    coverage: 4000,
    segments: 18,
    apcad: 900,
    apcad_pcf: 44,
    haplotype: 22,
  },
  total_records: 11140,
  samples: [
    {
      sample_id: 'S1',
      role: 'proband',
      affected: true,
      sex: 'male',
      projects: ['P1'],
      track_counts: {
        coverage: 2000,
        segments: 9,
        apcad: 450,
        apcad_pcf: 22,
        haplotype: 11,
        structural_variants: 610,
        repeat_expansions: 0,
      },
      total_records: 3080,
    },
    {
      sample_id: 'S2',
      role: 'mother',
      affected: false,
      sex: 'female',
      projects: ['P1'],
      track_counts: {
        coverage: 2000,
        segments: 9,
        apcad: 450,
        apcad_pcf: 22,
        haplotype: 11,
        structural_variants: 590,
        repeat_expansions: 0,
      },
      total_records: 3060,
    },
  ],
};

const rawFiles = {
  family_id: 'F1',
  family_files: [
    {
      id: 'rf1',
      scope: 'family',
      dataset: 'snv',
      file_name: 'F1.annotated.vcf.gz',
      file_type: 'VCF',
      sample_id: null,
      storage_path: '/data/F1/snv/F1.annotated.vcf.gz',
      file_size: 3221225472,
      sha256: 'a8d3f1bc00112233445566778899aabbccddeeff00112233445566778899aabb',
      source: 'family_package',
      created_at: '2026-06-01T00:00:00+00:00',
      exists: true,
      download_available: true,
    },
  ],
  individual_files: [
    {
      id: 'rf2',
      scope: 'individual',
      dataset: 'wisecondorx',
      file_name: 'S1_bins.bed',
      file_type: 'BED',
      sample_id: 'S1',
      storage_path: '/data/F1/wisecondorx/S1/bins.bed',
      file_size: 1048576,
      sha256: 'ffeeddccbbaa00998877665544332211ffeeddccbbaa00998877665544332211',
      source: 'family_package',
      created_at: '2026-06-01T00:00:00+00:00',
      exists: true,
      download_available: true,
    },
  ],
};

const mockResponses = () => {
  (api.get as unknown as Mock).mockImplementation((url: string) => {
    if (url === '/projects') {
      return Promise.resolve({
        data: [
          { id: 'P1', name: 'Proj1', families: [], samples: [] },
          { id: 'P2', name: 'Proj2', families: [], samples: [] },
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
        ],
      });
    }
    if (url === '/admin/data/families/F1') {
      return Promise.resolve({ data: familyDetail });
    }
    if (url === '/admin/data/families/F1/files') {
      return Promise.resolve({ data: rawFiles });
    }
    return Promise.resolve({ data: [] });
  });
};

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <DataManagementPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return queryClient;
};

const selectFamily = async () => {
  const familyButton = await screen.findByRole('button', { name: 'F1' });
  fireEvent.click(familyButton);
};

describe('DataManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('drives an in-page family workspace from the grouped catalog selector', async () => {
    mockResponses();
    renderPage();

    expect(
      await screen.findByText('Family and sample data management'),
    ).toBeInTheDocument();

    await selectFamily();

    // Embedded, editable family workspace is reused for the overview.
    expect(await screen.findByTestId('embedded-family-detail')).toBeInTheDocument();

    // Single deletion action remains; the removed actions are gone.
    expect(screen.getByRole('button', { name: 'Delete entire family' })).toBeInTheDocument();
    expect(screen.queryByText('Delete family small variants')).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: /edit family details/i }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole('link', { name: /open family workspace/i }),
    ).not.toBeInTheDocument();

    // Consolidated sample management + family-level project access.
    expect(screen.getByText('Family statistics & sample management')).toBeInTheDocument();
    expect(screen.getAllByText('Delete sample').length).toBeGreaterThan(0);
    expect(screen.getByText('Link family to projects')).toBeInTheDocument();
    expect(screen.getAllByText('Proj1').length).toBeGreaterThan(0);

    // Raw data provenance with grouped family/individual files.
    expect(await screen.findByText('Raw data provenance')).toBeInTheDocument();
    expect(screen.getByText('Family-level files')).toBeInTheDocument();
    expect(screen.getByText('Individual-level files')).toBeInTheDocument();
    expect(screen.getByText('F1.annotated.vcf.gz')).toBeInTheDocument();
    expect(screen.getByText('S1_bins.bed')).toBeInTheDocument();
  });

  it('requires typing the family name before the entire family can be deleted', async () => {
    mockResponses();
    (api.delete as unknown as Mock).mockResolvedValue({ data: { ok: true } });
    renderPage();

    await selectFamily();

    fireEvent.click(await screen.findByRole('button', { name: 'Delete entire family' }));

    const continueButton = screen.getByRole('button', { name: 'Continue' });
    expect(continueButton).toBeDisabled();

    fireEvent.change(screen.getByPlaceholderText('F1'), { target: { value: 'F1' } });
    expect(continueButton).not.toBeDisabled();
    fireEvent.click(continueButton);

    const confirmButton = screen.getByRole('button', { name: /permanently delete F1/i });
    fireEvent.click(confirmButton);

    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith('/admin/families/F1', {
        params: { confirm: true },
      }),
    );
  });

  it('invalidates shared project and family catalogs after saving family project access', async () => {
    mockResponses();
    (api.put as unknown as Mock).mockResolvedValue({ data: { ok: true } });

    const queryClient = createTestQueryClient();
    const invalidateSpy = vi
      .spyOn(queryClient, 'invalidateQueries')
      .mockResolvedValue(undefined);

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter>
          <DataManagementPage />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await selectFamily();

    expect(await screen.findByText('Save project access')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('checkbox', { name: 'Proj2' }));
    fireEvent.click(screen.getByRole('button', { name: 'Save project access' }));

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith('/admin/families/F1/projects', {
        project_ids: ['P1', 'P2'],
      }),
    );

    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['admin', 'data-inventory'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['projects'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['families'] });
    expect(invalidateSpy).toHaveBeenCalledWith({ queryKey: ['family', 'F1'] });
  });
});
