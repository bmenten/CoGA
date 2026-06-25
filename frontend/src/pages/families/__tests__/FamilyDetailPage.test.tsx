import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, it, vi } from 'vitest';

import FamilyDetailPage from '../FamilyDetailPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import api from '../../../lib/api';

/**
 * The dashboard's variant links/buttons load behind a separate
 * "Checking available family data…" query than the family record. Tests that
 * assert on those must wait for this gate to clear after the family name appears
 * — otherwise the assertion races the variant-availability load, which failed on
 * the slower CI runner twice (PRs #197, #199). Call this right after awaiting the
 * family name, then query variant links/buttons synchronously.
 */
const waitForVariantWorkspaceReady = () =>
  waitFor(() =>
    expect(screen.queryByText(/Checking available family data/i)).not.toBeInTheDocument(),
  );

const mockApiState = vi.hoisted(() => ({
  smallVariantTotal: 1,
  structuralVariantTotal: 1,
  hpoAnnotations: [] as unknown[],
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            _id: 'fam1',
            family_id: 'F1',
            members: [{ sample_id: 'S1', role: 'proband', affected: true, sex: 'male' }],
            pedigree: null,
            projects: ['p1'],
            metadata: {},
            status: { key: 'analysis_in_progress', label: 'Analysis in progress', color: '#2f6fb0' },
            assigned_to: {
              id: 'u1',
              username: 'ann',
              email: 'ann@example.com',
              first_name: 'Ann',
              last_name: 'Lee',
            },
            reviewed_by: null,
            roi: {
              query: 'GENE1',
              label: 'GENE1',
              source: 'gene',
              assembly_id: 'asm1',
              chr: '17',
              start: 43044295,
              end: 43125482,
            },
          },
        });
      }
      if (url === '/families/F1/hpo') {
        return Promise.resolve({ data: mockApiState.hpoAnnotations });
      }
      if (url === '/families/F1/members/S1') {
        return Promise.resolve({
          data: {
            member: {
              sample_id: 'S1',
              role: 'proband',
              affected: true,
              sex: 'male',
              clinical_status: 'affected',
              carrier_status: 'unknown',
            },
            father_id: null,
            mother_id: null,
            hpo_annotations: mockApiState.hpoAnnotations,
            impact: {
              sample_id: 'S1',
              pedigree_references: {},
              data_counts: {},
              affected_analysis_scopes: [],
              stale_analysis_scopes: [],
              warnings: [],
              destructive: false,
              requires_manual_recalculation: false,
            },
          },
        });
      }
      if (url === '/hpo/search') {
        return Promise.resolve({
          data: [{ hpo_id: 'HP:0001250', label: 'Seizure', definition: null, is_obsolete: false }],
        });
      }
      if (url.startsWith('/families/F1/structural-variants')) {
        return Promise.resolve({ data: { total: mockApiState.structuralVariantTotal, variants: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { total: mockApiState.smallVariantTotal, variants: [] } });
      }
      if (url === '/families/F1/small-variant-review-summary') {
        return Promise.resolve({
          data: {
            reviewed_variant_count: 3,
            note_count: 2,
            tag_counts: {
              review: 2,
              send_for_validation: 1,
              acmg_class_4: 1,
            },
          },
        });
      }
      if (url === '/families/F1/small-variant-tags') {
        return Promise.resolve({
          data: [
            {
              key: 'review',
              label: 'Review',
              group: 'collaboration',
              color: '#2563eb',
              sort_order: 10,
              scope: 'system',
              is_custom: false,
            },
            {
              key: 'send_for_validation',
              label: 'Send for validation',
              group: 'collaboration',
              color: '#b7791f',
              sort_order: 20,
              scope: 'system',
              is_custom: false,
            },
            {
              key: 'acmg_class_4',
              label: 'Likely Pathogenic - class 4',
              group: 'classification',
              color: '#ea580c',
              sort_order: 120,
              scope: 'system',
              is_custom: false,
            },
            {
              key: 'needs_segmentation_review',
              label: 'Needs segmentation review',
              group: 'collaboration',
              color: '#7c3aed',
              sort_order: 25,
              scope: 'family',
              is_custom: true,
            },
          ],
        });
      }
      if (url === '/families/F1/structural-variant-review-summary') {
        return Promise.resolve({
          data: {
            reviewed_variant_count: 2,
            note_count: 1,
            tag_counts: {
              review: 1,
              needs_segmentation_review: 1,
            },
          },
        });
      }
      if (url === '/projects') {
        return Promise.resolve({
          data: [{ _id: 'p1', name: 'Oncology pilot', species_id: 'sp1', assembly_id: 'asm1' }],
        });
      }
      if (url === '/species') {
        return Promise.resolve({
          data: [{ _id: 'sp1', name: 'Homo sapiens', common_name: 'human' }],
        });
      }
      if (url === '/assemblies/sp1') {
        return Promise.resolve({
          data: [{ _id: 'asm1', assembly_name: 'GRCh38', version: 'p14' }],
        });
      }
      if (url === '/family-statuses') {
        return Promise.resolve({
          data: [
            { id: 'st1', key: 'solved', label: 'Solved', color: '#1f9d57', sort_order: 10, is_active: true },
            {
              id: 'st2',
              key: 'analysis_in_progress',
              label: 'Analysis in progress',
              color: '#2f6fb0',
              sort_order: 50,
              is_active: true,
            },
          ],
        });
      }
      if (url === '/users') {
        return Promise.resolve({
          data: [
            { id: 'u1', username: 'ann', email: 'ann@example.com', first_name: 'Ann', last_name: 'Lee' },
            { id: 'u2', username: 'bob', email: 'bob@example.com', first_name: 'Bob', last_name: 'Ng' },
          ],
        });
      }
      return Promise.resolve({ data: [] });
    }),
    put: vi.fn(),
    post: vi.fn(),
    delete: vi.fn(),
  },
}));

describe('FamilyDetailPage', () => {
  beforeEach(() => {
    localStorage.clear();
    mockApiState.smallVariantTotal = 1;
    mockApiState.structuralVariantTotal = 1;
    mockApiState.hpoAnnotations = [];
    vi.mocked(api.put).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it('lets a signed-in user change and save the family status', async () => {
    localStorage.setItem('role', 'viewer');
    vi.mocked(api.put).mockResolvedValue({
      data: {
        _id: 'fam1',
        family_id: 'F1',
        members: [{ sample_id: 'S1', role: 'proband', affected: true, sex: 'male' }],
        projects: ['p1'],
        metadata: {},
        status: { key: 'solved', label: 'Solved', color: '#1f9d57' },
        assigned_to: { id: 'u1', username: 'ann', email: 'ann@example.com', first_name: 'Ann', last_name: 'Lee' },
        reviewed_by: null,
      },
    });
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const statusSelect = (await screen.findByLabelText('Family status')) as HTMLSelectElement;
    await waitFor(() => expect(statusSelect.value).toBe('analysis_in_progress'));
    // Changing a picker auto-saves just that field (no explicit save button).
    fireEvent.change(statusSelect, { target: { value: 'solved' } });

    await waitFor(() =>
      expect(api.put).toHaveBeenCalledWith(
        '/families/F1/metadata',
        expect.objectContaining({ status_key: 'solved' }),
      ),
    );
  });

  it('lets admins edit the ROI from the family dashboard without structure edit controls', async () => {
    localStorage.setItem('role', 'admin');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    expect(screen.getByText(/GENE1/i)).toBeInTheDocument();
    expect(screen.getByText(/chr17:43,044,295-43,125,482/i)).toBeInTheDocument();
    expect(screen.getByText(/Oncology pilot/i)).toBeInTheDocument();
    expect(
      screen.getByRole('link', { name: /structural variants/i })
    ).toHaveAttribute('href', '/families/F1/structural-variants?project_id=p1');
    expect(
      screen.getByRole('link', { name: /small variants/i })
    ).toHaveAttribute('href', '/families/F1/small-variants?project_id=p1');
    // ROI is editable from the family dashboard for admins.
    expect(screen.getByPlaceholderText(/Gene or locus/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /^save$/i })).toBeInTheDocument();
    // Structure editing stays gated on the editable structure view only.
    expect(screen.queryByLabelText('Carrier status for S1')).not.toBeInTheDocument();
    // Curation is shown beneath each variant type's link, scoped to that type.
    await waitFor(() => {
      const smallVariantCuration = screen.getByLabelText(/small variant curation/i);
      expect(
        within(smallVariantCuration).getByText((_, element) => element?.textContent?.trim() === 'Reviewed 3'),
      ).toBeInTheDocument();
      expect(
        within(smallVariantCuration).getByText((_, element) => element?.textContent?.trim() === 'Notes 2'),
      ).toBeInTheDocument();
      expect(
        within(smallVariantCuration).getByText((_, element) => element?.textContent?.trim() === 'Review 2'),
      ).toBeInTheDocument();
      expect(
        within(smallVariantCuration).getByText(
          (_, element) => element?.textContent?.trim() === 'Send for validation 1',
        ),
      ).toBeInTheDocument();
      const structuralVariantCuration = screen.getByLabelText(/structural variant curation/i);
      expect(
        within(structuralVariantCuration).getByText((_, element) => element?.textContent?.trim() === 'Reviewed 2'),
      ).toBeInTheDocument();
      expect(
        within(structuralVariantCuration).getByText(
          (_, element) => element?.textContent?.trim() === 'Needs segmentation review 1',
        ),
      ).toBeInTheDocument();
    });
  });

  it('does not expose ROI edit controls to non-admin viewers', async () => {
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    // ROI summary is visible, but the edit controls are admin-only.
    expect(screen.getByText(/chr17:43,044,295-43,125,482/i)).toBeInTheDocument();
    expect(
      screen.queryByPlaceholderText(/BRCA1 or chr17:43044295-43125482/i),
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /save roi/i })).not.toBeInTheDocument();
  });

  it('omits every variant workspace when no variant data is loaded', async () => {
    mockApiState.smallVariantTotal = 0;
    mockApiState.structuralVariantTotal = 0;
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    expect(screen.getByText(/No variant data is loaded for this family yet/i)).toBeInTheDocument();
    // Workspaces with no underlying data are omitted entirely — neither a link nor a button.
    for (const name of [
      /small variants/i,
      /structural variants/i,
      /repeat expansions/i,
      /paraphase/i,
      /mtDNA analysis/i,
      /variant summary/i,
    ]) {
      expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
  });

  it('shows only the variant workspaces backed by data and omits the rest', async () => {
    mockApiState.smallVariantTotal = 2;
    mockApiState.structuralVariantTotal = 0;
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    // Small variants present -> small-variants + variant-summary links are shown.
    expect(screen.getByRole('link', { name: /small variants/i })).toHaveAttribute(
      'href',
      '/families/F1/small-variants?project_id=p1',
    );
    expect(screen.getByRole('link', { name: /variant summary/i })).toBeInTheDocument();
    // No structural / repeat / paraphase / mtDNA data -> omitted entirely (no link, no button).
    for (const name of [
      /structural variants/i,
      /repeat expansions/i,
      /paraphase/i,
      /mtDNA analysis/i,
    ]) {
      expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
  });

  it('shows HPO phenotype annotations in the family members overview', async () => {
    mockApiState.hpoAnnotations = [
      {
        id: 'hpo1',
        sample_id: 'S1',
        hpo_id: 'HP:0001250',
        label: 'Seizure',
        definition: 'A seizure phenotype.',
        status: 'present',
        onset: null,
        evidence: null,
        source: 'manual',
        note: 'Observed',
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ];
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    expect(screen.queryByRole('heading', { name: /phenotypes/i })).not.toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /hpo terms/i })).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByText('Seizure')).toBeInTheDocument();
      expect(screen.getByTitle(/HP:0001250: A seizure phenotype/i)).toBeInTheDocument();
    });
  });

  it('opens family member details with editable HPO controls for admins', async () => {
    mockApiState.hpoAnnotations = [
      {
        id: 'hpo1',
        sample_id: 'S1',
        hpo_id: 'HP:0001250',
        label: 'Seizure',
        definition: 'A seizure phenotype.',
        status: 'present',
        onset: null,
        evidence: null,
        source: 'manual',
        note: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ];
    localStorage.setItem('role', 'admin');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'S1' }));

    expect(await screen.findByRole('dialog', { name: /family member details/i })).toBeInTheDocument();
    expect(await screen.findByLabelText(/identifier/i)).toHaveValue('S1');
    expect(screen.getByLabelText('Phenotype')).toHaveValue('affected');
    expect(screen.getByLabelText('Carrier status')).toHaveValue('unknown');
    expect(screen.getByRole('heading', { name: /hpo phenotypes/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /add phenotype/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /apply to pending/i })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /remove member/i })).toBeInTheDocument();
  });

  it('queues member metadata edits and saves them in one batch', async () => {
    localStorage.setItem('role', 'admin');
    vi.mocked(api.put).mockResolvedValueOnce({
      data: {
        family: {
          _id: 'fam1',
          family_id: 'F1',
          members: [
            {
              sample_id: 'S1',
              role: 'proband',
              affected: false,
              sex: 'male',
              clinical_status: 'unknown',
              carrier_status: 'carrier',
            },
          ],
          relationships: [],
          structure_version: { version: 2 },
          pedigree: null,
          projects: ['p1'],
          metadata: {},
          roi: null,
        },
        warnings: ['Phenotype and carrier labels were updated; downstream interpretation views were marked stale without reloading raw datasets.'],
      },
    });
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'S1' }));
    fireEvent.change(await screen.findByLabelText('Carrier status'), {
      target: { value: 'carrier' },
    });
    fireEvent.click(screen.getByRole('button', { name: /apply to pending/i }));
    expect(await screen.findByText(/1 pending/i)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /save pending updates/i }));

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/families/F1/members/batch',
      expect.objectContaining({
        change_reason: 'family_member_batch_update',
        updates: [
          expect.objectContaining({
            sample_id: 'S1',
            carrier_status: 'carrier',
          }),
        ],
      }),
    ));
    const batchPayload = vi.mocked(api.put).mock.calls.find(
      ([url]) => url === '/families/F1/members/batch',
    )?.[1] as { updates: Array<Record<string, unknown>> };
    expect(batchPayload.updates[0]).not.toHaveProperty('father_id');
    expect(batchPayload.updates[0]).not.toHaveProperty('mother_id');
    expect(batchPayload.updates[0]).not.toHaveProperty('sex');
    expect(batchPayload.updates[0]).not.toHaveProperty('role');
    expect(await screen.findByText(/Pending updates saved/i)).toBeInTheDocument();
  });

  it('preserves the selected project in variant workspace links', async () => {
    localStorage.setItem('role', 'viewer');
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1?project_id=p1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    expect(
      screen.getByRole('link', { name: /structural variants/i })
    ).toHaveAttribute('href', '/families/F1/structural-variants?project_id=p1');
    expect(
      screen.getByRole('link', { name: /small variants/i })
    ).toHaveAttribute('href', '/families/F1/small-variants?project_id=p1');
  });

  it('saves family structure edits with carrier state and explicit couples', async () => {
    localStorage.setItem('role', 'admin');
    vi.mocked(api.put).mockResolvedValueOnce({
      data: {
        family: {
          _id: 'fam1',
          family_id: 'F1',
          members: [
            {
              sample_id: 'S1',
              role: 'proband',
              affected: true,
              sex: 'male',
              clinical_status: 'affected',
              carrier_status: 'carrier',
              carrier_type: 'proven',
            },
            {
              sample_id: 'S2',
              role: 'relative',
              affected: false,
              sex: 'female',
              clinical_status: 'unknown',
              carrier_status: 'unknown',
            },
          ],
          relationships: [
            {
              id: 'r1',
              relationship_type: 'couple',
              sample_id_a: 'S1',
              sample_id_b: 'S2',
              role_a: 'partner',
              role_b: 'partner',
              metadata: {},
            },
          ],
          structure_version: { version: 2 },
          pedigree: null,
          projects: ['p1'],
          metadata: {},
          roi: null,
        },
        warnings: ['Relationship-dependent analyses should be rerun before clinical review.'],
      },
    });
    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/admin/data/families/F1/structure']}>
          <Routes>
            <Route path="/admin/data/families/:familyId/structure" element={<FamilyDetailPage editable />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    // The structure-editor form renders a tick after the family name; await its
    // first control so the test does not race it on slower CI runners.
    fireEvent.change(await screen.findByLabelText('Carrier status for S1'), {
      target: { value: 'carrier' },
    });
    fireEvent.change(screen.getByLabelText('New member sample ID'), { target: { value: 'S2' } });
    fireEvent.change(screen.getByLabelText('New member sex'), { target: { value: 'female' } });
    fireEvent.click(screen.getByRole('button', { name: /add member/i }));
    // Partner is now selected inline on the member row instead of a separate table.
    fireEvent.change(screen.getByLabelText('Partner for S1'), { target: { value: 'S2' } });
    fireEvent.click(screen.getByRole('button', { name: /update family structure/i }));

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/families/F1/structure',
      expect.objectContaining({
        clear_existing_genomic_data: false,
        add_members: [
          expect.objectContaining({
            sample_id: 'S2',
            sex: 'female',
          }),
        ],
        members: [
          expect.objectContaining({
            sample_id: 'S1',
            carrier_status: 'carrier',
          }),
        ],
        relationships: expect.objectContaining({
          couples: [
            expect.objectContaining({
              partners: ['S1', 'S2'],
            }),
          ],
        }),
      }),
    ));
    expect(await screen.findByText(/Family structure saved/i)).toBeInTheDocument();
  });
});
