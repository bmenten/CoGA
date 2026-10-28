import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, it, vi, type Mock } from 'vitest';

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
  members: [{ sample_id: 'S1', role: 'proband', affected: true, sex: 'male' }] as unknown[],
  relationships: [] as unknown[],
  analysisType: null as string | null,
  // The caller is a reader in the family's projects (SEC-1).
  readOnly: false,
  // Whom the case pickers offer: the family's analysts and the administrators.
  assignableUsers: [] as unknown[],
  // How many repeat loci the family has calls at (the presence check).
  repeatLociCount: 0,
}));

const ANN = { id: 'u1', username: 'ann', email: 'ann@example.com', first_name: 'Ann', last_name: 'Lee' };
const BOB = { id: 'u2', username: 'bob', email: 'bob@example.com', first_name: 'Bob', last_name: 'Ng' };

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            _id: 'fam1',
            family_id: 'F1',
            members: mockApiState.members,
            relationships: mockApiState.relationships,
            pedigree: null,
            projects: ['p1'],
            read_only: mockApiState.readOnly,
            metadata: {
              pipeline: { genome: 'GRCh38', snv_caller: 'deepvariant' },
              ...(mockApiState.analysisType ? { analysis_type: mockApiState.analysisType } : {}),
            },
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
      if (url === '/families/F1/assignable-users') {
        return Promise.resolve({ data: mockApiState.assignableUsers });
      }
      if (url.startsWith('/families/F1/repeat-expansions')) {
        return Promise.resolve({ data: { samples: [], loci: [], loci_count: mockApiState.repeatLociCount } });
      }
      if (url === '/families/F1/finding-review-summary') {
        return Promise.resolve({
          data: {
            repeat: { reviewed_count: 2, classified_count: 1, note_count: 1, tag_counts: { review: 2 } },
            paraphase: { reviewed_count: 0, classified_count: 0, note_count: 0, tag_counts: {} },
          },
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
    mockApiState.members = [
      { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
    ];
    mockApiState.relationships = [];
    mockApiState.analysisType = null;
    mockApiState.readOnly = false;
    mockApiState.assignableUsers = [ANN, BOB];
    mockApiState.repeatLociCount = 0;
    vi.mocked(api.put).mockReset();
    vi.mocked(api.post).mockReset();
    vi.mocked(api.delete).mockReset();
  });

  it('shows the analysis-pipeline settings last, collapsed', async () => {
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

    // The pipeline block is recorded on the family at package import. The panel is a
    // closed <details> here, so its content is in the DOM but not shown until opened.
    const panel = await screen.findByTestId('pipeline-settings');
    expect((panel as HTMLDetailsElement).open).toBe(false);
    expect(panel).toHaveTextContent('GRCh38');
    expect(panel).toHaveTextContent('deepvariant');
    // Last on the page: context for what is above it, not a starting point.
    const members = screen.getByText('Family members').closest('section');
    expect(members).not.toBeNull();
    expect(members!.compareDocumentPosition(panel) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
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

  const renderFamily = () =>
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

  const optionTexts = (select: HTMLElement) =>
    Array.from((select as HTMLSelectElement).options).map((option) => option.textContent);

  it("offers the family's analysts and administrators in the assignee and reviewer pickers", async () => {
    renderFamily();

    const assigned = await screen.findByRole('combobox', { name: 'Assigned to' });
    await waitFor(() => expect(optionTexts(assigned)).toEqual(['Unassigned', 'Ann Lee', 'Bob Ng']));
    expect(optionTexts(screen.getByRole('combobox', { name: 'Reviewed by' }))).toEqual([
      'Not reviewed',
      'Ann Lee',
      'Bob Ng',
    ]);
    // The family's own list, not every user of CoGA (a reader among them).
    expect(api.get).toHaveBeenCalledWith('/families/F1/assignable-users');
    expect(api.get).not.toHaveBeenCalledWith('/users');
    expect(assigned).not.toHaveAttribute('title');
  });

  it('keeps an assignee who is no longer eligible, marked, until it is changed', async () => {
    // Ann was assigned before she became a reader: the family's list no longer holds her.
    mockApiState.assignableUsers = [BOB];
    vi.mocked(api.put).mockResolvedValue({ data: { _id: 'fam1', family_id: 'F1', members: [], projects: ['p1'] } });
    renderFamily();

    const assigned = await screen.findByRole('combobox', { name: 'Assigned to' });
    await waitFor(() => expect(assigned).toHaveDisplayValue('Ann Lee (not eligible)'));
    expect(assigned).toHaveValue('u1');
    expect(assigned).toBeEnabled();
    expect(assigned).toHaveAttribute('title', expect.stringMatching(/^Not eligible: .* It stays until you change it\.$/));
    expect(optionTexts(assigned)).toEqual(['Unassigned', 'Bob Ng', 'Ann Lee (not eligible)']);
    // Nobody chose her again: the reviewer picker offers the list alone.
    expect(optionTexts(screen.getByRole('combobox', { name: 'Reviewed by' }))).toEqual(['Not reviewed', 'Bob Ng']);
    expect(api.put).not.toHaveBeenCalled();

    fireEvent.change(assigned, { target: { value: 'u2' } });
    await waitFor(() => expect(api.put).toHaveBeenCalledWith('/families/F1/metadata', { assigned_to: 'u2' }));
  });

  it('says why the server refused an assignee, and keeps the stored one', async () => {
    vi.mocked(api.put).mockRejectedValue({
      isAxiosError: true,
      response: {
        status: 422,
        data: {
          detail:
            "Bob Ng cannot be assigned this case: they are a reader in this family's projects. Choose an analyst of one of the family's projects or an administrator.",
        },
      },
    });
    renderFamily();

    const assigned = await screen.findByRole('combobox', { name: 'Assigned to' });
    await waitFor(() => expect(assigned).toHaveValue('u1'));
    fireEvent.change(assigned, { target: { value: 'u2' } });

    expect(await screen.findByText(/Bob Ng cannot be assigned this case: they are a reader/)).toBeInTheDocument();
    await waitFor(() => expect(assigned).toHaveValue('u1'));
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
      /monogenic nipt/i,
      /variant summary/i,
    ]) {
      expect(screen.queryByRole('link', { name })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument();
    }
  });

  it('offers the monogenic NIPT analysis to a family whose analysis type is monogenic_nipt', async () => {
    mockApiState.smallVariantTotal = 0;
    mockApiState.structuralVariantTotal = 0;
    mockApiState.analysisType = 'monogenic_nipt';
    localStorage.setItem('role', 'viewer');

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    expect(screen.getByRole('link', { name: /monogenic nipt/i })).toHaveAttribute(
      'href',
      expect.stringContaining('/families/F1/nipt'),
    );
    // Its data is the NIPT analysis, so it is not told that no variant data is loaded.
    expect(screen.queryByText(/No variant data is loaded for this family yet/i)).not.toBeInTheDocument();
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
    // Small variants present -> the small-variants link is shown.
    expect(screen.getByRole('link', { name: /small variants/i })).toHaveAttribute(
      'href',
      '/families/F1/small-variants?project_id=p1',
    );
    // No structural / repeat / paraphase / mtDNA data -> omitted entirely (no link, no button).
    // The variant summary summarises the structural variants, so it has nothing to show
    // for small variants only.
    for (const name of [
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

  it('offers the variant summary for a family with structural variants only', async () => {
    mockApiState.smallVariantTotal = 0;
    mockApiState.structuralVariantTotal = 2;
    localStorage.setItem('role', 'viewer');

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();
    expect(screen.getByRole('link', { name: 'Variant summary' })).toHaveAttribute(
      'href',
      '/families/F1/variant-summary',
    );
    expect(screen.queryByRole('link', { name: /small variants/i })).not.toBeInTheDocument();
  });

  it('lays the analyses out as one row, curation below it and the family actions in the header', async () => {
    mockApiState.smallVariantTotal = 2;
    mockApiState.structuralVariantTotal = 2;
    localStorage.setItem('role', 'viewer');

    const { container } = render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();

    // One row of equal entries, in order; the variant summary comes last, at the row's
    // right end.
    const analyses = screen.getByRole('navigation', { name: 'Variant analyses' });
    expect(within(analyses).getAllByRole('link').map((link) => link.textContent?.trim())).toEqual([
      'Small variants',
      'Structural variants',
      'Variant summary',
    ]);
    for (const link of within(analyses).getAllByRole('link')) {
      expect(link).toHaveClass('family-module-link');
    }
    expect(within(analyses).getByRole('link', { name: 'Variant summary' })).toHaveClass('family-module-link--end');

    // The curation is its own block after the row, not interleaved with its links.
    const curation = container.querySelector('.family-curation-summary') as HTMLElement;
    expect(curation).not.toBeNull();
    expect(within(curation).queryAllByRole('link')).toHaveLength(0);
    expect(analyses.compareDocumentPosition(curation) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    // Sample QC and the report are family-level: in the header, beside the workflow fields.
    const actions = screen.getByRole('group', { name: 'Family actions' });
    expect(actions.closest('.page-top-card')).not.toBeNull();
    expect(within(actions).getAllByRole('link').map((link) => link.textContent?.trim())).toEqual([
      'Sample QC',
      'Report',
    ]);
    expect(within(actions).getByRole('link', { name: 'Sample QC' })).toHaveAttribute('href', '/families/F1/qc');
    expect(within(analyses).queryByRole('link', { name: /sample qc|report/i })).toBeNull();
  });

  it('counts the repeat reviews on a curation line of their own', async () => {
    mockApiState.repeatLociCount = 3;
    localStorage.setItem('role', 'viewer');
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const line = await screen.findByRole('group', { name: 'Repeat expansions curation' });
    await waitFor(() => expect(line).toHaveTextContent('Reviewed 2'));
    expect(line).toHaveTextContent('Classified 1');
    expect(line).toHaveTextContent('Notes 1');
    expect(within(line).getByText('Review')).toBeInTheDocument();
    // No Paraphase results: no line for them.
    expect(screen.queryByRole('group', { name: 'Paraphase curation' })).not.toBeInTheDocument();
  });

  it('offers the report but not Sample QC without small variants', async () => {
    mockApiState.smallVariantTotal = 0;
    mockApiState.structuralVariantTotal = 2;
    localStorage.setItem('role', 'viewer');

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();

    const actions = screen.getByRole('group', { name: 'Family actions' });
    expect(within(actions).getByRole('link', { name: 'Report' })).toBeInTheDocument();
    expect(within(actions).queryByRole('link', { name: 'Sample QC' })).toBeNull();
    expect(screen.queryByLabelText(/small variant curation/i)).not.toBeInTheDocument();
    expect(screen.getByLabelText(/structural variant curation/i)).toBeInTheDocument();
  });

  it('gives the visualization links the same row look as the analyses', async () => {
    mockApiState.smallVariantTotal = 2;
    localStorage.setItem('role', 'viewer');

    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    await waitForVariantWorkspaceReady();

    // Genome, Chromosome, Circos and IGV are the same control as the analyses' row.
    const views = screen.getByRole('navigation', { name: 'Genome views' });
    for (const name of [/genome/i, /chromosome/i, /circos/i, /igv/i]) {
      expect(within(views).getByRole('link', { name })).toHaveClass('family-module-link');
    }
  });

  it('states the region on the title line and hides ROI markers off a PGT case', async () => {
    localStorage.setItem('role', 'viewer');
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());

    // With a region set the card says so beside its own heading rather than below it.
    const heading = await screen.findByText('Region of interest');
    const headline = heading.closest('.family-roi-headline') as HTMLElement;
    expect(headline).not.toBeNull();
    expect(within(headline).getByRole('link', { name: /GENE1/ })).toBeInTheDocument();

    // This family has no embryo, so there are no phased markers to review.
    expect(screen.queryByRole('link', { name: /review roi markers/i })).not.toBeInTheDocument();
  });

  it('offers ROI markers on a PGT case', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'S1', role: 'mother', affected: false, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'unknown' },
    ];
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    expect(await screen.findByRole('link', { name: /review roi markers/i })).toHaveAttribute(
      'href',
      '/families/F1/roi-markers?project_id=p1',
    );
  });

  // The embryo calls the backend makes (GET /families/{id}/pgt-analysis), as a sign-out freezes
  // them: the page shows each called embryo's badge and warnings as served. The rules behind the
  // calls are tested in backend/tests/test_pgt_embryo_calls.py.
  const pgtAnalysis = (
    embryos: Record<string, unknown>[],
    overrides: Record<string, unknown> = {},
  ): Record<string, unknown> => ({
    region_of_interest: { query: 'GENE1', label: 'GENE1', source: 'gene', chrom: '17', start: 43044295, end: 43125482 },
    window: { chrom: '17', start: 42044295, end: 44125482 },
    inheritance_model: { recorded: 'AD', applied: 'AD', mode: 'dominant' },
    members: [],
    disease_haplotype_resolved: true,
    disease_haplotypes: [{ origin: 'paternal', homolog: '1', kind: 'dominant' }],
    phase_corrections: [],
    embryos: embryos.map((embryo) => ({
      sex: 'female',
      roi_not_covered: false,
      sex_dependent: null,
      inherited: [],
      recombinations: [],
      qc: null,
      qc_verdict: null,
      ...embryo,
    })),
    inputs: { haplotype_sources: [], blocks_read: 0, blocks_digest: null, markers_truncated: false, blocks_outdated: false },
    rules: {
      window_flank: 1_000_000,
      recombination_flank: 250_000,
      phase_switch_uncertainty: 2_000_000,
      qc_thresholds: { min_informative_sites: 50, max_mendel_rate: 0.02, min_markers_per_side: 6 },
    },
    ...overrides,
  });

  const renderWithAnalysis = (analysis: Record<string, unknown> | Error) => {
    const get = api.get as unknown as Mock;
    const working = get.getMockImplementation()!;
    get.mockImplementation((url: string, config?: unknown) =>
      url === '/families/F1/pgt-analysis'
        ? analysis instanceof Error
          ? Promise.reject(analysis)
          : Promise.resolve({ data: analysis })
        : working(url, config),
    );
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    return () => get.mockImplementation(working);
  };

  const rowOf = async (sampleId: string) =>
    (await screen.findByRole('button', { name: sampleId })).closest('tr') as HTMLElement;

  // #607 — the embryos' recombination and uninformative warnings come with the calls: a
  // failed request is not an embryo without warnings.
  it('says the segregation could not be derived when the embryo calls at the ROI fail', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'S1', role: 'mother', affected: false, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'unknown' },
    ];
    const restore = renderWithAnalysis(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
    try {
      expect(await screen.findByText(/Could not load the embryo calls at the ROI — this is not an empty result/)).toHaveTextContent(
        /segregation, and any recombination or uninformative warning, are not shown/,
      );
      expect(screen.getByText('⚠ segregation not derived')).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  it('asks for the embryo calls only for a family with embryos and an ROI', async () => {
    localStorage.setItem('role', 'viewer');
    (api.get as unknown as Mock).mockClear();
    const restore = renderWithAnalysis(pgtAnalysis([]));
    try {
      await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
      await waitForVariantWorkspaceReady();
      expect((api.get as unknown as Mock).mock.calls.some(([url]) => url === '/families/F1/pgt-analysis')).toBe(false);
    } finally {
      restore();
    }
  });

  // No data at the ROI is not an unaffected embryo. The embryo badge used to read
  // "Unaffected" for an embryo whose haplotype has no block over the ROI.
  // CLIN-66: blocks built for another pedigree or other sexes: the page warns, and each embryo's
  // uninformative call says why.
  it('warns that the haplotype blocks are outdated and says so beside each embryo', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis([{ sample: 'E1', call: 'uninformative' }], {
        disease_haplotype_resolved: false,
        disease_haplotypes: [],
        inputs: { haplotype_sources: [], blocks_read: 4, blocks_digest: 'abc', markers_truncated: false, blocks_outdated: true },
      }),
    );
    try {
      const warning =
        "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.";
      expect(await screen.findByText(`⚠ ${warning}`)).toHaveClass('status-note--warning');
      const e1 = await rowOf('E1');
      expect(await within(e1).findByText('Uninformative')).toHaveClass('segregation-badge--uninformative');
      expect(within(e1).getByRole('button', { name: warning })).toHaveTextContent('⚠ uninformative');
    } finally {
      restore();
    }
  });

  it('reads an embryo the backend calls uninformative for missing data as such, not unaffected', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E2', role: 'embryo', affected: false, sex: 'female' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis([
        { sample: 'E1', call: 'uninformative', roi_not_covered: true },
        { sample: 'E2', call: 'unaffected_non_carrier' },
      ]),
    );
    try {
      const e1 = await rowOf('E1');
      expect(await within(e1).findByText('Uninformative')).toHaveClass('segregation-badge--uninformative');
      expect(within(e1).queryByText('Unaffected')).not.toBeInTheDocument();
      // The warning says why: this embryo's data, not an unresolved disease haplotype.
      expect(
        within(e1).getByRole('button', { name: /this embryo's haplotype does not cover the ROI/i }),
      ).toHaveTextContent('⚠ uninformative');

      // An embryo seen across the ROI keeps its call.
      expect(within(await rowOf('E2')).getByText('Unaffected')).toHaveClass(
        'segregation-badge--unaffected_non_carrier',
      );
    } finally {
      restore();
    }
  });

  it('says when no disease haplotype was resolved, and warns of a recombination near the ROI', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E2', role: 'embryo', affected: false, sex: 'female' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis(
        [
          { sample: 'E1', call: 'uninformative' },
          { sample: 'E2', call: 'uninformative', recombinations: [43_100_000] },
        ],
        { disease_haplotype_resolved: false, disease_haplotypes: [] },
      ),
    );
    try {
      const e1 = await rowOf('E1');
      expect(
        await within(e1).findByRole('button', { name: /No disease haplotype could be resolved at the ROI/ }),
      ).toHaveTextContent('⚠ uninformative');
      const e2 = await rowOf('E2');
      expect(within(e2).getByRole('button', { name: /A recombination falls inside or close to the ROI/ })).toHaveTextContent(
        '⚠ recombination',
      );
    } finally {
      restore();
    }
  });

  // X-linked recessive: an embryo whose sex is not recorded and which carries the mother's
  // risk X is affected if male and a carrier if female. Its badge used to read "Carrier".
  it("does not call an X-linked embryo of unrecorded sex a carrier when a son would be affected", async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'SON', role: 'proband', affected: true, sex: 'male' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'unknown' },
      { sample_id: 'E2', role: 'embryo', affected: false, sex: 'unknown' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis(
        [
          {
            sample: 'E1',
            sex: 'und',
            call: 'affected_or_at_risk',
            sex_dependent: { if_male: 'affected_or_at_risk', if_female: 'carrier' },
          },
          { sample: 'E2', sex: 'und', call: 'unaffected_non_carrier' },
        ],
        {
          inheritance_model: { recorded: 'XLR', applied: 'XLR', mode: 'x_linked_recessive' },
          disease_haplotypes: [{ origin: 'maternal', homolog: '1', kind: 'x-linked' }],
        },
      ),
    );
    try {
      const e1 = await rowOf('E1');
      expect(await within(e1).findByText('Affected / at risk')).toHaveClass('segregation-badge--affected_or_at_risk');
      expect(within(e1).queryByText('Carrier')).not.toBeInTheDocument();
      // The warning gives both calls, so recording the sex resolves it.
      const bothCalls = /sex is not recorded.*affected \/ at risk if male, carrier if female/i;
      expect(within(e1).getByRole('button', { name: bothCalls })).toHaveTextContent('⚠ sex unknown');

      // The other maternal X is unaffected whatever the sex: no warning.
      const e2 = await rowOf('E2');
      expect(within(e2).getByText('Unaffected')).toHaveClass('segregation-badge--unaffected_non_carrier');
      expect(within(e2).queryByText('⚠ sex unknown')).not.toBeInTheDocument();
    } finally {
      restore();
    }
  });

  // A parent's phase switch the blocks undid lies next to the ROI: the embryos' calls stand,
  // but where the phase switched is known only to within the children's switches.
  it("warns on every embryo when a parent's phase was corrected next to the ROI", async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'DAD', role: 'father', affected: true, sex: 'male' },
      { sample_id: 'KID', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E2', role: 'embryo', affected: false, sex: 'female' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis(
        [
          { sample: 'E1', call: 'affected_or_at_risk' },
          { sample: 'E2', call: 'unaffected_non_carrier' },
        ],
        {
          phase_corrections: [
            { parent: 'DAD', side: 'father', chr: '7', position: 30_400_000, end: 30_450_000, children_switching: 3, children: 3 },
          ],
        },
      ),
    );
    try {
      for (const embryo of ['E1', 'E2']) {
        const row = await rowOf(embryo);
        const warning = await within(row).findByRole('button', { name: /Phase corrected: 3 of 3 children switched together on the father's side/ });
        expect(warning).toHaveTextContent('⚠ phase corrected');
      }
      // The call itself stands: E1 shares the affected haplotype, E2 does not.
      expect(within(await rowOf('E1')).getByText('Affected / at risk')).toBeInTheDocument();
      expect(within(await rowOf('E2')).getByText('Unaffected')).toBeInTheDocument();
    } finally {
      restore();
    }
  });

  // The owner's decision of 2026-10-20: an embryo call whose phasing QC at the ROI does not meet
  // its thresholds (backend/app/services/pgt_embryo_qc.py) gets a clear warning beside it; the
  // call stays shown and the analyst decides.
  it('warns beside an embryo call whose phasing QC does not meet its thresholds, and keeps the call', async () => {
    localStorage.setItem('role', 'viewer');
    mockApiState.members = [
      { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E2', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E3', role: 'embryo', affected: false, sex: 'female' },
    ];
    const restore = renderWithAnalysis(
      pgtAnalysis([
        {
          sample: 'E1',
          call: 'affected_or_at_risk',
          qc: { informative_sites: 6, mendel_errors: 1, mendel_rate: 1 / 6, paternal_markers: 3, maternal_markers: 3 },
          qc_verdict: {
            status: 'warning',
            sides: ['paternal'],
            findings: [
              { measure: 'informative_sites', value: 6, threshold: 50 },
              { measure: 'mendel_rate', value: 1 / 6, threshold: 0.02 },
              { measure: 'paternal_markers', value: 3, threshold: 6 },
            ],
          },
        },
        { sample: 'E2', call: 'unaffected_non_carrier', qc_verdict: { status: 'pass', sides: ['paternal'], findings: [] } },
        { sample: 'E3', call: 'unaffected_non_carrier', qc_verdict: { status: 'not_counted', sides: ['paternal'], findings: [] } },
      ]),
    );
    try {
      const e1 = await rowOf('E1');
      // The call is shown as made, with the warning beside it.
      expect(await within(e1).findByText('Affected / at risk')).toHaveClass('segregation-badge--affected_or_at_risk');
      // In words, not colour alone: the chip says it, and its accessible name names each
      // measure with its value and its threshold.
      const warning = within(e1).getByRole('button', { name: /The phasing QC at the ROI does not meet its thresholds/ });
      expect(warning).toHaveTextContent('⚠ phasing QC');
      expect(warning).toHaveAccessibleName(
        "The phasing QC at the ROI does not meet its thresholds: 6 informative sites (fewer than 50); Mendelian error rate 16.7% (above 2%); 3 markers on the father's side (fewer than 6). The call stands: review the ROI markers before relying on it.",
      );

      // A QC that meets its thresholds raises nothing.
      const e2 = await rowOf('E2');
      expect(within(e2).getByText('Unaffected')).toBeInTheDocument();
      expect(within(e2).queryByText(/phasing QC|QC not counted/)).not.toBeInTheDocument();

      // A QC that was not counted is no pass: the call was not checked.
      const e3 = await rowOf('E3');
      expect(within(e3).getByText('Unaffected')).toBeInTheDocument();
      expect(
        within(e3).getByRole('button', {
          name: /QC at the ROI was not counted \(the embryo is not recorded as a child of the couple\), so this call was not checked/,
        }),
      ).toHaveTextContent('⚠ QC not counted');
    } finally {
      restore();
    }
  });

  // #607 — a failed request on the family page is said as such, never as missing data.
  describe('when a request fails', () => {
    const renderWithFailing = (matches: (url: string) => boolean) => {
      const get = api.get as unknown as Mock;
      const working = get.getMockImplementation()!;
      get.mockImplementation((url: string, config?: unknown) =>
        matches(url)
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : working(url, config),
      );
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/F1']}>
            <Routes>
              <Route path="/families/:familyId" element={<FamilyDetailPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
      return () => get.mockImplementation(working);
    };

    it('shows a workspace whose presence check failed, and says the check failed', async () => {
      mockApiState.structuralVariantTotal = 0;
      const restore = renderWithFailing((url) => url.startsWith('/families/F1/structural-variants?count_only'));
      try {
        expect(
          await screen.findByText(/Could not load whether this family has structural variants — this is not an empty result/),
        ).toHaveTextContent(/Their links are shown, so you can open them/);
        // Unknown, not absent: the link is offered, and the check does not "run" for good.
        expect(screen.getByRole('link', { name: 'Structural variants' })).toBeInTheDocument();
        expect(screen.queryByText('Checking available family data…')).not.toBeInTheDocument();
      } finally {
        restore();
      }
    });

    it('offers the variant summary when the structural-variant check failed, even without small variants', async () => {
      mockApiState.smallVariantTotal = 0;
      const restore = renderWithFailing((url) => url.startsWith('/families/F1/structural-variants?count_only'));
      try {
        await screen.findByText(/Could not load whether this family has structural variants/);
        // Whether there is anything to summarise is unknown, so the page is offered to say so.
        expect(screen.getByRole('link', { name: 'Variant summary' })).toBeInTheDocument();
      } finally {
        restore();
      }
    });

    it('reads failed curation counts and HPO terms as unknown, not as none', async () => {
      const restore = renderWithFailing(
        (url) => url === '/families/F1/small-variant-review-summary' || url === '/families/F1/hpo',
      );
      try {
        const curation = await screen.findByLabelText('Small variant curation');
        await waitFor(() => expect(curation).toHaveTextContent('Reviewed —'));
        expect(curation).toHaveTextContent('Notes —');
        expect(await screen.findByText('could not be loaded')).toBeInTheDocument();
      } finally {
        restore();
      }
    });

    it('keeps the current status shown, and unchangeable, when the status list failed', async () => {
      const restore = renderWithFailing((url) => url === '/family-statuses');
      try {
        expect(await screen.findByText(/Could not load the status list — this is not an empty result/)).toHaveTextContent(
          /The current values are shown, and cannot be changed until it loads/,
        );
        const status = screen.getByRole('combobox', { name: 'Family status' });
        expect(status).toHaveValue('analysis_in_progress');
        expect(status).toHaveDisplayValue('Analysis in progress');
        expect(status).toBeDisabled();
      } finally {
        restore();
      }
    });

    it('marks no assignee as not eligible when the list of eligible users failed', async () => {
      const restore = renderWithFailing((url) => url === '/families/F1/assignable-users');
      try {
        expect(await screen.findByText(/Could not load the user list — this is not an empty result/)).toBeInTheDocument();
        const assigned = screen.getByRole('combobox', { name: 'Assigned to' });
        // Whether Ann is still eligible is unknown: she is shown as she is stored.
        expect(assigned).toHaveDisplayValue('Ann Lee');
        expect(assigned).toBeDisabled();
      } finally {
        restore();
      }
    });

    // #610 — a server error is not a family that does not exist.
    it('says the family could not be loaded, not that it was not found, and retries', async () => {
      const restore = renderWithFailing((url) => url === '/families/F1');
      try {
        expect(await screen.findByRole('heading', { name: 'Family could not be loaded' })).toBeInTheDocument();
        expect(screen.getByText('HTTP 500 This is a failed request, not a missing family.')).toBeInTheDocument();
        expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
      } finally {
        restore();
      }
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText(/Family F1/i)).toBeInTheDocument();
    });

    it('says the member details could not be loaded, instead of loading for good', async () => {
      const restore = renderWithFailing((url) => url === '/families/F1/members/S1');
      try {
        await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
        fireEvent.click(screen.getByRole('button', { name: 'S1' }));
        const dialog = await screen.findByRole('dialog', { name: /family member details/i });
        expect(
          await within(dialog).findByText(/Could not load the member's details — this is not an empty result/),
        ).toBeInTheDocument();
        expect(within(dialog).queryByText('Loading member details.')).not.toBeInTheDocument();
      } finally {
        restore();
      }
    });
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

  it('asks before closing member details over unapplied edits, not once applied (#529)', async () => {
    localStorage.setItem('role', 'admin');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
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

    fireEvent.keyDown(document, { key: 'Escape' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByRole('dialog', { name: /family member details/i })).toBeInTheDocument();

    // Applied edits wait among the pending updates, so closing now loses nothing.
    fireEvent.click(screen.getByRole('button', { name: /apply to pending/i }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('dialog', { name: /family member details/i })).not.toBeInTheDocument();
    expect(screen.getByText(/1 pending/i)).toBeInTheDocument();
    confirm.mockRestore();
  });

  // #528: the member dialog's own flows, recorded before it moved into its own component.
  const openS1AsAdmin = async () => {
    localStorage.setItem('role', 'admin');
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'S1' }));
    const dialog = await screen.findByRole('dialog', { name: /family member details/i });
    // The member's details load after the dialog opens.
    await within(dialog).findByLabelText(/identifier/i);
    return dialog;
  };

  it('adds a phenotype to a member from the HPO search', async () => {
    vi.mocked(api.post).mockResolvedValueOnce({ data: {} });
    const dialog = await openS1AsAdmin();

    // A term is picked once the search has answered: type, then choose the match.
    fireEvent.change(within(dialog).getByLabelText('HPO term'), { target: { value: 'HP:000125' } });
    await waitFor(() =>
      expect(dialog.querySelector('datalist option[value]')).not.toBeNull(),
    );
    fireEvent.change(within(dialog).getByLabelText('HPO term'), { target: { value: 'HP:0001250' } });
    fireEvent.change(within(dialog).getByLabelText(/^note$/i), { target: { value: ' seen at 4 y ' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /add phenotype/i }));

    await waitFor(() =>
      expect(api.post).toHaveBeenCalledWith('/families/F1/members/S1/hpo', {
        hpo_id: 'HP:0001250',
        status: 'present',
        source: 'manual',
        note: 'seen at 4 y',
      }),
    );
    expect(await within(dialog).findByText('Phenotype annotation saved.')).toBeInTheDocument();
    expect(within(dialog).getByLabelText('HPO term')).toHaveValue('');
  });

  it('says so when a phenotype cannot be saved', async () => {
    vi.mocked(api.post).mockRejectedValueOnce(new Error('boom'));
    const dialog = await openS1AsAdmin();
    fireEvent.change(within(dialog).getByLabelText('HPO term'), { target: { value: 'HP:000125' } });
    await waitFor(() => expect(dialog.querySelector('datalist option[value]')).not.toBeNull());
    fireEvent.change(within(dialog).getByLabelText('HPO term'), { target: { value: 'HP:0001250' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /add phenotype/i }));

    expect(await within(dialog).findByText(/boom|Failed to save phenotype annotation/)).toBeInTheDocument();
  });

  it('removes a phenotype from a member', async () => {
    mockApiState.hpoAnnotations = [
      {
        id: 'hpo1',
        sample_id: 'S1',
        hpo_id: 'HP:0001250',
        label: 'Seizure',
        definition: null,
        status: 'present',
        onset: null,
        evidence: null,
        source: 'manual',
        note: null,
        created_at: '2026-01-01T00:00:00Z',
        updated_at: '2026-01-01T00:00:00Z',
      },
    ];
    vi.mocked(api.delete).mockResolvedValueOnce({ data: {} });
    const dialog = await openS1AsAdmin();

    fireEvent.click(await within(dialog).findByRole('button', { name: /^remove$/i }));

    await waitFor(() => expect(api.delete).toHaveBeenCalledWith('/families/F1/hpo/hpo1'));
    expect(await within(dialog).findByText('Phenotype annotation removed.')).toBeInTheDocument();
  });

  // SEC-1: an analyst of the family's project, no admin, adds and removes phenotypes; a reader
  // sees them and the workflow fields but changes neither.
  const openS1As = async (role: string) => {
    localStorage.setItem('role', role);
    render(
      <QueryClientProvider client={createTestQueryClient()}>
        <MemoryRouter initialEntries={['/families/F1']}>
          <Routes>
            <Route path="/families/:familyId" element={<FamilyDetailPage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
    await waitFor(() => expect(screen.getByText(/Family F1/i)).toBeInTheDocument());
    fireEvent.click(screen.getByRole('button', { name: 'S1' }));
    const dialog = await screen.findByRole('dialog', { name: /family member details/i });
    await within(dialog).findByLabelText(/identifier/i);
    return dialog;
  };
  const seizure = {
    id: 'hpo1',
    sample_id: 'S1',
    hpo_id: 'HP:0001250',
    label: 'Seizure',
    definition: null,
    status: 'present',
    onset: null,
    evidence: null,
    source: 'manual',
    note: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  };

  it('lets an analyst who is no administrator add and remove phenotypes', async () => {
    mockApiState.hpoAnnotations = [seizure];
    const dialog = await openS1As('viewer');

    expect(await within(dialog).findByRole('button', { name: /^remove$/i })).toBeEnabled();
    expect(within(dialog).getByRole('button', { name: /add phenotype/i })).toBeInTheDocument();
    expect(within(dialog).getByLabelText('HPO term')).toBeInTheDocument();
    // The member's own fields stay an administrator's.
    expect(within(dialog).queryByRole('button', { name: /apply to pending/i })).not.toBeInTheDocument();
  });

  it('shows a reader the phenotypes and the workflow fields but no control to change them', async () => {
    mockApiState.readOnly = true;
    mockApiState.hpoAnnotations = [seizure];
    const dialog = await openS1As('viewer');

    expect(await within(dialog).findByText('Seizure')).toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /^remove$/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByRole('button', { name: /add phenotype/i })).not.toBeInTheDocument();
    expect(within(dialog).queryByLabelText('HPO term')).not.toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });

    for (const name of ['Family status', 'Assigned to', 'Reviewed by']) {
      expect(screen.getByRole('combobox', { name })).toBeDisabled();
    }
    expect(screen.getByText('Read-only access (reader)')).toBeInTheDocument();
  });

  it('does not offer to remove the family’s last member', async () => {
    const dialog = await openS1AsAdmin();
    expect(within(dialog).getByRole('button', { name: /remove member/i })).toBeDisabled();
  });

  it('removes a member only once the removal is confirmed', async () => {
    mockApiState.members = [
      { sample_id: 'S1', role: 'proband', affected: true, sex: 'male' },
      { sample_id: 'S2', role: 'mother', affected: false, sex: 'female' },
    ];
    const confirm = vi.spyOn(window, 'confirm').mockReturnValueOnce(false).mockReturnValueOnce(true);
    vi.mocked(api.delete).mockResolvedValueOnce({
      data: {
        family: {
          _id: 'fam1',
          family_id: 'F1',
          members: [],
          relationships: [],
          pedigree: null,
          projects: ['p1'],
          metadata: {},
          roi: null,
        },
      },
    });
    const dialog = await openS1AsAdmin();

    fireEvent.click(within(dialog).getByRole('button', { name: /remove member/i }));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(confirm.mock.calls[0][0]).toContain('Remove S1 from the active family?');
    expect(api.delete).not.toHaveBeenCalled();

    fireEvent.click(within(dialog).getByRole('button', { name: /remove member/i }));
    await waitFor(() =>
      expect(api.delete).toHaveBeenCalledWith('/families/F1/members/S1', { params: { confirm: true } }),
    );
    await waitFor(() =>
      expect(screen.queryByRole('dialog', { name: /family member details/i })).not.toBeInTheDocument(),
    );
    confirm.mockRestore();
  });

  it('keeps a member edit pending after Apply, and says so', async () => {
    const dialog = await openS1AsAdmin();
    fireEvent.change(within(dialog).getByLabelText('Carrier status'), { target: { value: 'carrier' } });
    fireEvent.click(within(dialog).getByRole('button', { name: /apply to pending/i }));

    expect(
      await within(dialog).findByText('Member changes are pending. Save pending updates to commit them together.'),
    ).toBeInTheDocument();
    // Reopened, the dialog shows the pending edit, not the saved value.
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'S1' }));
    expect(await screen.findByLabelText('Carrier status')).toHaveValue('carrier');
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

  it('shows a link of unknown degree and saves it to both parents', async () => {
    localStorage.setItem('role', 'admin');
    mockApiState.members = [
      { sample_id: 'DAD', role: 'father', affected: false, sex: 'male' },
      { sample_id: 'MOM', role: 'mother', affected: true, sex: 'female' },
      { sample_id: 'EMB1', role: 'embryo', affected: false, sex: 'und' },
      { sample_id: 'INDEX1', role: 'relative', affected: true, sex: 'female' },
    ];
    const parentChild = (parent: string, role: string) => ({
      id: `pc-${parent}`,
      relationship_type: 'parent_child',
      sample_id_a: parent,
      sample_id_b: 'EMB1',
      role_a: role,
      role_b: 'child',
      metadata: {},
    });
    mockApiState.relationships = [
      parentChild('DAD', 'father'),
      parentChild('MOM', 'mother'),
      {
        id: 'rel-1',
        relationship_type: 'relative',
        sample_id_a: 'MOM',
        sample_id_b: 'INDEX1',
        role_a: 'relative',
        role_b: 'relative',
        metadata: {},
      },
    ];
    vi.mocked(api.put).mockResolvedValueOnce({
      data: { family: { _id: 'fam1', family_id: 'F1', members: mockApiState.members, projects: ['p1'], metadata: {} } },
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

    const related = (await screen.findByLabelText('Related to, for INDEX1')) as HTMLSelectElement;
    await waitFor(() => expect(related.value).toBe('MOM'));
    fireEvent.change(related, { target: { value: 'DAD,MOM' } });
    expect(related.selectedOptions[0].textContent).toBe('both parents');
    fireEvent.click(screen.getByRole('button', { name: /update family structure/i }));

    await waitFor(() => expect(api.put).toHaveBeenCalledWith(
      '/families/F1/structure',
      expect.objectContaining({
        relationships: expect.objectContaining({
          relatives: [
            { member: 'INDEX1', related_to: 'DAD', metadata: {} },
            { member: 'INDEX1', related_to: 'MOM', metadata: {} },
          ],
        }),
      }),
    ));
  });
});
