import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyReportPage from '../FamilyReportPage';
import { APP_VERSION_QUERY_KEY } from '../../../lib/appVersion';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  // GET /version, the build every report footer names (TF-15 §1). Answered here for every
  // test, so each test's fake backend serves only the family.
  version: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: (...args: unknown[]) => (args[0] === '/version' ? apiMock.version() : apiMock.get(...args)),
    post: apiMock.post,
  },
}));

const RUNNING_BUILD = { version: '0.2.0', git_sha: '0123456789abcdef' };

const GRCH38_REFERENCE = {
  speciesName: 'Homo sapiens',
  assemblyName: 'GRCh38',
  assemblyValidated: true as boolean | undefined,
  assemblyVersion: 'p14',
  projectId: undefined,
  isLoading: false,
};
const referenceMock = vi.hoisted(() => ({ value: undefined as unknown }));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => referenceMock.value,
  formatResolvedReferenceLabel: ({ assemblyName }: { assemblyName?: string }) =>
    assemblyName || 'Not linked',
}));

const reportVariant = {
  _id: 'v1',
  chr: 'chr17',
  start: 43000000,
  end: 43000000,
  type: 'snv',
  ref: 'A',
  alt: 'G',
  gene: 'BRCA1',
  effect: 'missense_variant',
  hgvsc: 'c.123A>G',
  hgvsp: 'p.Lys41Arg',
  clinvar: 'Pathogenic',
  gnomad_af: 0,
  cadd_phred: 28.1,
  genotypes: [
    { sample: 'PROBAND', gt: '0/1' },
    { sample: 'FATHER', gt: '0/0' },
  ],
  review: {
    variant_id: 'v1',
    tags: ['report', 'acmg_class_4'],
    tag_metadata: {},
    note: 'Strong candidate for the reported phenotype.',
    acmg: {
      criteria: [
        { code: 'PM2', strength: 'moderate', accepted: true, auto_suggested: true },
        { code: 'PP3', strength: 'supporting', accepted: true, auto_suggested: true },
      ],
      point_total: 3,
      classification: 'Likely Pathogenic - class 4',
    },
  },
};

const mockApi = () => {
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/F1') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          members: [
            { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
            { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
          ],
          projects: [],
        },
      });
    }
    if (url.startsWith('/families/F1/small-variants')) {
      return Promise.resolve({ data: { variants: [reportVariant], total: 1 } });
    }
    if (url === '/genes/profile') {
      return Promise.resolve({
        data: {
          symbol: 'BRCA1',
          display_name: 'BRCA1 DNA repair associated',
          summary: 'BRCA1 is a tumour suppressor involved in DNA double-strand break repair.',
          panels: [{ panel_id: 'p1', name: 'Hereditary cancer' }],
          extra: {
            hpo_terms: [{ hpo_id: 'HP:0003002', label: 'Breast carcinoma' }],
            gencc_assertions: [
              { disease_title: 'Breast-ovarian cancer, familial', moi_title: 'Autosomal dominant' },
            ],
          },
        },
      });
    }
    if (url === '/families/F1/hpo') {
      return Promise.resolve({
        data: [
          { sample_id: 'PROBAND', hpo_id: 'HP:0003002', label: 'Breast carcinoma', status: 'present' },
        ],
      });
    }
    if (url === '/families/F1/annotation-manifest') {
      return Promise.resolve({
        data: {
          family_id: 'F1',
          assembly: 'GRCh38',
          source: 'manual',
          recorded_at: null,
          recorded_by: null,
          modules: [
            { key: 'assembly', label: 'Reference assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference' },
            { key: 'clinvar', label: 'ClinVar', version: '2026-05', detail: null, layer: 'pipeline' },
          ],
        },
      });
    }
    return Promise.resolve({ data: {} });
  });
};

const renderPage = (queryClient = createTestQueryClient(), path = '/families/F1/report') =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/families/:familyId/report" element={<FamilyReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const reportFooter = async () =>
  (await screen.findByText(/Report generated .* UTC/)).closest('footer') as HTMLElement;

describe('FamilyReportPage', () => {
  beforeEach(() => {
    referenceMock.value = GRCH38_REFERENCE;
    apiMock.version.mockResolvedValue({ data: RUNNING_BUILD });
  });

  it('drafts a report for each reported variant with description, criteria, gene and HPO', async () => {
    mockApi();
    renderPage();

    expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();

    // Variant description prose mentions zygosity, consequence and absence from gnomAD.
    expect(screen.getAllByText(/heterozygous/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/absent from gnomAD/).length).toBeGreaterThan(0);

    // ACMG motivation writes out the accepted criteria.
    expect(screen.getByText('PM2')).toBeInTheDocument();
    expect(screen.getByText('PP3')).toBeInTheDocument();
    expect(screen.getAllByText(/Likely Pathogenic - class 4/).length).toBeGreaterThan(0);

    // Gene description and HPO overlap.
    expect(screen.getByText(/tumour suppressor/)).toBeInTheDocument();
    expect(screen.getByText(/phenotype overlaps/)).toBeInTheDocument();
    expect(screen.getByText(/Breast carcinoma \(HP:0003002\)/)).toBeInTheDocument();

    // Analyst note surfaced.
    expect(screen.getByText(/Strong candidate for the reported phenotype\./)).toBeInTheDocument();

    // Provenance footer: a generation timestamp + the annotation/reference versions.
    expect(await screen.findByText(/Report generated .* UTC/)).toBeInTheDocument();
    expect(screen.getByText(/ClinVar 2026-05/)).toBeInTheDocument();
    expect(screen.getByText(/Reference assembly GRCh38 \(2013-12-01\)/)).toBeInTheDocument();
  });

  // TF-15 §1: the version is "in every report footer" — it was only in the sign-out block of
  // a signed report.
  it('names the running build and the device label in the report footer', async () => {
    mockApi();
    renderPage();

    const footer = await reportFooter();
    expect(await within(footer).findByText('CoGA 0.2.0 (0123456)')).toBeInTheDocument();
    expect(footer).toHaveTextContent('Software: CoGA 0.2.0 (0123456)');
    expect(footer).toHaveTextContent(
      'In-house IVD per IVDR Article 5(5) · Not CE-marked · For internal CMGG use only',
    );
    expect(footer).toHaveTextContent(
      'Manufacturer: Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent',
    );
    // A report whose build is known prints as complete.
    expect(screen.queryByText(/so this printout does not show the whole report/)).not.toBeInTheDocument();
  });

  it('asks for the running build each time the report is opened, never showing the cached one', async () => {
    mockApi();
    let answer: (value: unknown) => void = () => undefined;
    apiMock.version.mockReturnValue(new Promise((resolve) => (answer = resolve)));
    const queryClient = createTestQueryClient();
    // What the app footer read before a redeploy.
    queryClient.setQueryData([...APP_VERSION_QUERY_KEY], { version: '0.1.0', git_sha: 'fedcba9876543210' });
    renderPage(queryClient);

    // Until the server answers, the report names no build: not the cached one.
    const footer = await reportFooter();
    expect(within(footer).getByText('CoGA — loading the version…')).toBeInTheDocument();
    expect(screen.queryByText(/CoGA 0\.1\.0/)).not.toBeInTheDocument();
    expect(apiMock.version).toHaveBeenCalledTimes(1);

    answer({ data: RUNNING_BUILD });
    expect(await within(footer).findByText('CoGA 0.2.0 (0123456)')).toBeInTheDocument();
    expect(screen.queryByText(/CoGA 0\.1\.0/)).not.toBeInTheDocument();
  });

  it('shows an empty state when no variants are tagged for reporting', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/No variants are currently tagged for reporting/)).toBeInTheDocument();
  });

  it('warns when a classification’s evidence has drifted since it was made', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/classification-drift') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            checked: 2,
            drifted_count: 1,
            drifted: [
              {
                variant_id: '1-100-A-G',
                acmg_class: 'acmg_class_4',
                classified_by: 'alice',
                classified_at: null,
                status: 'drifted',
                annotation_version_from: 'v1',
                annotation_version_to: 'v2',
                clinvar_from: 'Uncertain significance',
                clinvar_to: 'Pathogenic',
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    const alert = await screen.findByRole('alert');
    expect(alert).toBeInTheDocument();
    expect(screen.getByText('1-100-A-G')).toBeInTheDocument();
    expect(
      screen.getByText(/ClinVar Uncertain significance → Pathogenic/),
    ).toBeInTheDocument();
    expect(screen.getByText(/classified by alice/)).toBeInTheDocument();
  });

  it('lists the structural variants and CNVs whose classification evidence moved', async () => {
    const evidence = {
      source: 'needlr',
      sv_type: 'DEL',
      chrom: '18',
      start: 55000000,
      end: 55400000,
      gene_symbols: ['TCF4', 'TXNL1'],
      gene_count: 2,
      pli: 0.99,
      inheritance: 'de_novo',
    };
    const svItem = (variantId: string, overrides: Record<string, unknown>) => ({
      variant_id: variantId,
      classification: 'Pathogenic - class 5',
      cnv_class: 'cnv_class_5',
      classified_by: 'bob',
      classified_at: null,
      status: 'drifted',
      changed: [],
      evidence_from: evidence,
      evidence_to: evidence,
      ...overrides,
    });
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/classification-drift') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            checked: 0,
            drifted_count: 0,
            drifted: [],
            structural: {
              checked: 4,
              drifted_count: 4,
              drifted: [
                svItem('sv-genes', {
                  changed: ['gene_symbols', 'pli'],
                  evidence_to: { ...evidence, gene_symbols: ['TCF4'], gene_count: 1, pli: 0.41 },
                }),
                svItem('sv-annotation', { changed: ['annotations'] }),
                svItem('sv-gone', { status: 'variant_missing', evidence_to: null }),
                svItem('sv-unreadable', { status: 'unknown', evidence_from: null }),
              ],
            },
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    // One banner counts the SV/CNV classifications with the small variants.
    expect(
      await screen.findByText(/4 classifications have evidence changes since being made/),
    ).toBeInTheDocument();
    const items = screen.getAllByRole('listitem').map((item) => item.textContent ?? '');
    expect(items).toContain(
      'sv-genes (structural variant) — genes TCF4, TXNL1 → TCF4; pLI 0.990 → 0.410 (classified by bob)',
    );
    expect(items).toContain('sv-annotation (structural variant) — annotation changed (classified by bob)');
    expect(items).toContain(
      'sv-gone (structural variant) — no longer present in the dataset (classified by bob)',
    );
    expect(items).toContain(
      'sv-unreadable (structural variant) — its frozen evidence cannot be compared (classified by bob)',
    );
  });

  it('renders the immutable clinical audit trail', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/clinical-audit') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            events: [
              {
                id: 'e1',
                created_at: '2026-06-25T09:04:00Z',
                variant_id: '1-100-A-G',
                actor: 'alice',
                action: 'classification',
                summary: 'Classification unclassified → VUS (class 3)',
                before: null,
                after: null,
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText('Classification audit trail')).toBeInTheDocument();
    expect(
      screen.getByText(/Classification unclassified → VUS \(class 3\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText(/2026-06-25 09:04 UTC/)).toBeInTheDocument();
  });

  function mockUnsignedFamily() {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({ data: { family_id: 'F1', latest: null, signouts: [] } });
      }
      return Promise.resolve({ data: [] });
    });
  }

  const QC_409 = {
    response: {
      status: 409,
      data: {
        detail: {
          gate: 'sample_qc',
          message: "Sample-integrity QC status is 'fail' (possible swap)",
          qc_summary: {
            overall_status: 'fail',
            messages: ['Relatedness: FATHER-CHILD looks unrelated (kinship 0.01)'],
          },
        },
      },
    },
  };

  it('blocks sign-out on a failing Sample QC and requires a reason to override', async () => {
    mockUnsignedFamily();
    const posts: Array<Record<string, unknown>> = [];
    apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
      posts.push(body);
      return posts.length === 1
        ? Promise.reject(QC_409)
        : Promise.resolve({ data: { version: 1 } });
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));

    // The failing QC opens the acknowledge-with-reason dialog with the backend message
    // + the failing summary.
    expect(
      await screen.findByRole('dialog', { name: /acknowledgement required/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/possible swap/)).toBeInTheDocument();
    expect(screen.getByText(/FATHER-CHILD looks unrelated/)).toBeInTheDocument();

    // "Sign out anyway" is disabled until a non-empty reason is typed.
    expect(screen.getByRole('button', { name: /Sign out anyway/ })).toBeDisabled();
    fireEvent.change(screen.getByLabelText(/Reason for signing out/), {
      target: { value: 'Repeat genotyping confirms identity' },
    });
    const overrideBtn = screen.getByRole('button', { name: /Sign out anyway/ });
    expect(overrideBtn).toBeEnabled();
    fireEvent.click(overrideBtn);

    // The override re-posts with acknowledge_qc + the reason.
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toMatchObject({
      acknowledge_qc: true,
      qc_acknowledgement_reason: 'Repeat genotyping confirms identity',
    });
  });

  it('labels an off-scope assembly and does not offer sign-out (#515)', async () => {
    referenceMock.value = {
      ...GRCH38_REFERENCE,
      assemblyName: 'T2T-CHM13v2.0',
      assemblyValidated: false,
    };
    mockUnsignedFamily();
    renderPage();

    expect(await screen.findByText(/Not validated for clinical use/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign out report/ })).toBeDisabled();
  });

  it('shows the scope refusal as a refusal, never as the drift override (#515)', async () => {
    mockUnsignedFamily();
    apiMock.post.mockRejectedValue({
      response: {
        status: 409,
        data: {
          detail: {
            gate: 'assembly_scope',
            message: 'This family is on T2T-CHM13v2.0, which is not validated for clinical use.',
            assembly: 'T2T-CHM13v2.0',
            validated_assemblies: ['GRCh38'],
          },
        },
      },
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));

    expect(await screen.findByText(/Not signed out\./)).toBeInTheDocument();
    expect(screen.getByText(/T2T-CHM13v2\.0, which is not validated/)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  // A family whose import is queued or running, or whose variants another write holds, is
  // refused: no dialog offers to sign it out anyway.
  it.each([
    [
      'import_in_progress',
      'An import of this family’s data is in progress (import job job-7), so the report would be signed out from data that is incomplete or changing. Sign out once the import has finished.',
    ],
    [
      'variant_writes_in_progress',
      'This family’s variants are being written (by an import, an upload or a deletion), so the report would be signed out from data that is changing. Sign out once the write has finished.',
    ],
    ['a_gate_this_page_does_not_know', 'Refused for a reason this page was not built for.'],
  ])('shows the %s refusal as a refusal, never as an override', async (gate, message) => {
    mockUnsignedFamily();
    apiMock.post.mockRejectedValue({
      response: { status: 409, data: { detail: { gate, message } } },
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));

    expect(await screen.findByText(/Not signed out\./)).toBeInTheDocument();
    expect(screen.getByText(message)).toBeInTheDocument();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText(/Evidence has changed/)).not.toBeInTheDocument();
  });

  // #608 — the reported variants are read within the family's project: with the catalogue
  // failed the report used to render with none. It says it could not be prepared.
  it('does not render an empty report when the reference could not be loaded', async () => {
    const retry = vi.fn();
    referenceMock.value = {
      ...GRCH38_REFERENCE,
      assemblyName: undefined,
      assemblyValidated: undefined,
      isError: true,
      retry,
    };
    mockUnsignedFamily();
    renderPage();

    expect(await screen.findByText('Report could not be prepared')).toBeInTheDocument();
    expect(screen.getByText(/This is not a report without variants/)).toBeInTheDocument();
    expect(screen.queryByText(/This report summarises/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Sign out report/ })).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(retry).toHaveBeenCalledTimes(1);
  });

  it('shows no scope label for a validated assembly', async () => {
    mockUnsignedFamily();
    renderPage();

    expect(await screen.findByRole('button', { name: /Sign out report/ })).toBeEnabled();
    expect(screen.queryByText(/Not validated for clinical use/)).not.toBeInTheDocument();
  });

  it('cancelling the QC override does not sign out', async () => {
    mockUnsignedFamily();
    const posts: Array<Record<string, unknown>> = [];
    apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
      posts.push(body);
      return Promise.reject(QC_409);
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
    await screen.findByRole('dialog', { name: /acknowledgement required/i });
    fireEvent.click(screen.getByRole('button', { name: /Cancel/ }));

    await waitFor(() =>
      expect(
        screen.queryByRole('dialog', { name: /acknowledgement required/i }),
      ).not.toBeInTheDocument(),
    );
    expect(posts).toHaveLength(1); // only the initial attempt; no override post
  });

  it('opens the acknowledgement dialog for an UNVERIFIABLE Sample QC (warn), not only a detected fail', async () => {
    // #330: a swap that manifests as missing data blocks with overall_status 'warn'
    // (not 'fail'); the dialog must still fire and show the specific reason.
    const UNVERIFIABLE_QC_409 = {
      response: {
        status: 409,
        data: {
          detail: {
            gate: 'sample_qc',
            message:
              'A swap-relevant sample-integrity check could not be verified for an asserted pedigree relationship (Relatedness of CHILD–FATHER could not be verified).',
            qc_summary: { overall_status: 'warn', messages: [] },
            unverifiable_checks: ['Relatedness of CHILD–FATHER could not be verified.'],
          },
        },
      },
    };
    mockUnsignedFamily();
    const posts: Array<Record<string, unknown>> = [];
    apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
      posts.push(body);
      return posts.length === 1
        ? Promise.reject(UNVERIFIABLE_QC_409)
        : Promise.resolve({ data: { version: 1 } });
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
    expect(
      await screen.findByRole('dialog', { name: /acknowledgement required/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/could not be verified/)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Reason for signing out/), {
      target: { value: 'Father not sequenced (deceased); relatedness not assessable.' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));

    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toMatchObject({ acknowledge_qc: true });
  });

  const checkResult = (overrides: Record<string, unknown>) =>
    Promise.resolve({
      data: {
        family_id: 'F1',
        version: 2,
        content_hash: 'abc123def456',
        matches: true,
        changed_sections: [],
        not_compared: [],
        checked_at: '2026-09-28T10:00:00Z',
        ...overrides,
      },
    });


  // REQ-TRACE-007, TF-06 H9: a signed version is rendered from its frozen record alone — never
  // from live data — and Print prints it. The live report is always labelled as not the
  // signed version, even when its content matches.
  const SIGNED_ENTRY = {
    version: 2,
    signed_out_by: 'bjorn',
    signed_out_at: '2026-06-25T10:00:00Z',
    content_hash: 'abc123def456',
    software_version: '0.1.0',
    git_sha: 'abc1234def567',
    qc_status: 'pass',
    qc_acknowledged: false,
    qc_acknowledgement_reason: null,
    drift_acknowledged: false,
    drift_acknowledgement_reason: null,
    import_incomplete_failed_datasets: null,
    import_incomplete_job_id: null,
    import_incomplete_acknowledged: false,
    import_incomplete_acknowledgement_reason: null,
    verified: null,
  };
  const V1_ENTRY = {
    ...SIGNED_ENTRY,
    version: 1,
    signed_out_by: 'alice',
    signed_out_at: '2026-06-20T09:00:00Z',
    content_hash: 'v1hash',
  };

  // What was signed. It differs from the live data of mockApi (BRCA1, its gene profile, its
  // note, ClinVar 2026-05), so anything live on the signed view would show.
  const SIGNED_SNAPSHOT = {
    family_id: 'F1',
    assembly: 'GRCh38',
    modules: [
      { key: 'assembly', label: 'Reference assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference' },
      { key: 'clinvar', label: 'ClinVar', version: '2026-04', detail: null, layer: 'pipeline' },
    ],
    reference_modules: ['assembly', 'gene_loci', 'monarch', 'hpo'],
    software: { version: '0.1.0', git_sha: 'abc1234def567' },
    drift: { checked: 1, drifted_count: 0, drifted: [] },
    structural_drift: { checked: 0, drifted_count: 0, drifted: [] },
    sample_qc: {
      overall_status: 'pass',
      application: 'wgs',
      application_label: 'Trio WGS',
      application_summary: '',
      genotype_source: null,
      sex_checks: [{ sample_id: 'PROBAND', status: 'pass', message: 'Recorded and inferred sex agree.' }],
      relatedness_checks: [],
      mendelian_checks: [],
      paternity_check: null,
      fetal_sex_check: null,
      category_qc_check: null,
      autosomal_sites: 10000,
      notes: [],
    },
    sequencing_qc: {
      profile_key: 'wgs',
      profile_label: 'WGS default',
      thresholds: {},
      samples: {
        PROBAND: {
          verdict: 'warn',
          metrics: [
            { metric_key: 'mean_coverage', label: 'Mean coverage', unit: '×', value: 18, warn_value: 20, error_value: 10, verdict: 'warn' },
          ],
          breached: ['mean_coverage'],
        },
      },
    },
    import_incomplete: null,
    reported_variants: [
      {
        variant_id: '17-43000000-A-G',
        acmg_class: 'acmg_class_4',
        acmg: {
          criteria: [
            { code: 'PS3', strength: 'strong', accepted: true, evidence: 'Functional assay shows loss of function.', auto_suggested: false },
            { code: 'PM2', strength: 'moderate', accepted: true, evidence: null, auto_suggested: true },
            { code: 'PP3', strength: 'supporting', accepted: true, evidence: null, auto_suggested: true },
            { code: 'BP4', strength: 'supporting', accepted: false, evidence: null, auto_suggested: true },
          ],
          point_total: 7,
          classification: 'Likely Pathogenic - class 4',
          vus_tier: null,
        },
        tags: ['acmg_class_4', 'report'],
        note: 'Interpretation as signed.',
        evidence_snapshot: {
          annotation_version: 'v1',
          annotation_set_hash: 'h1',
          clinvar: 'Likely_pathogenic',
          captured_at: '2026-06-20T08:30:00+00:00',
        },
      },
    ],
    reported_structural_variants: [
      {
        variant_id: '2-1000-50000-DEL---',
        variant_key: 123,
        classification: 'Pathogenic - class 5',
        cnv_class: 'cnv_class_5',
        cnv_point_total: 1,
        cnv_acmg: {
          kind: 'loss',
          criteria: [{ code: '2A', points: 1, accepted: true, evidence: 'Covers the HI region.', auto_suggested: false }],
          point_total: 1,
          classification: 'Pathogenic - class 5',
        },
        tags: ['report'],
        note: null,
      },
    ],
    version: 2,
    generated_at: '2026-06-25T10:00:00+00:00',
    signed_out_by: 'bjorn',
    acknowledged_drift: false,
    drift_acknowledgement_reason: null,
    acknowledged_qc: false,
    qc_acknowledgement_reason: null,
    acknowledged_import_incomplete: false,
    import_incomplete_acknowledgement_reason: null,
  };

  type SignedCase = {
    versions?: Array<Record<string, unknown>>;
    snapshot?: Record<string, unknown>;
    detail?: (version: number) => Promise<unknown>;
    check?: () => Promise<unknown>;
  };

  /** A signed case: the live data of mockApi, and signed versions whose records differ from it. */
  function mockSignedCase({
    versions = [SIGNED_ENTRY],
    snapshot = SIGNED_SNAPSHOT,
    detail,
    check = () => checkResult({ matches: true }),
  }: SignedCase = {}) {
    mockApi();
    const base = apiMock.get.getMockImplementation()!;
    apiMock.get.mockImplementation((url: string, config?: unknown) => {
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({ data: { family_id: 'F1', latest: versions[0], signouts: versions } });
      }
      if (url === '/families/F1/report/sign-out-check') return check();
      const signed = url.match(/^\/families\/F1\/report\/sign-outs\/(\d+)$/);
      if (signed) {
        const version = Number(signed[1]);
        if (detail) return detail(version);
        const entry = versions.find((candidate) => candidate.version === version);
        return entry
          ? Promise.resolve({
              data: { ...entry, verified: true, snapshot: { ...snapshot, version }, not_captured: [] },
            })
          : Promise.reject({ response: { status: 404, data: { detail: 'Sign-out version not found' } } });
      }
      return base(url, config);
    });
  }

  const LIVE_ONLY_REQUESTS = [
    '/families/F1/small-variants',
    '/families/F1/structural-variants',
    '/genes/profile',
    '/families/F1/hpo',
    '/families/F1/annotation-manifest',
    '/families/F1/classification-drift',
    '/families/F1/clinical-audit',
  ];

  const signedRecordCard = async (version = 2) =>
    (await screen.findByText(new RegExp(`^Signed version ${version} — signed out by`))).closest(
      'section',
    ) as HTMLElement;

  describe('a signed case', () => {
    it('opens on its latest signed version, rendered from the frozen record alone', async () => {
      mockSignedCase();
      const { container } = renderPage();

      const card = await signedRecordCard();
      expect(card).toHaveTextContent('Signed version 2 — signed out by bjorn on 2026-06-25 10:00 UTC');
      expect(card).toHaveTextContent('Content hash abc123def456');
      expect(card).toHaveTextContent('The stored record matches its content hash.');
      expect(card).toHaveTextContent('Signed with CoGA 0.1.0 (abc1234)');
      expect(card).toHaveTextContent('This is the latest signed version.');
      expect(screen.getByText('Clinical report — signed version 2')).toBeInTheDocument();
      expect(screen.getByText('Reference assembly as signed: GRCh38')).toBeInTheDocument();

      // The reported small variant as signed: classification, the accepted criteria, the
      // evidence frozen when it was classified, and the note.
      const variant = screen.getByRole('heading', { name: '17-43000000-A-G' }).closest('article')!;
      expect(variant).toHaveTextContent('Likely Pathogenic - class 4 · 7 pts');
      expect(variant).toHaveTextContent('based on PS3, PM2 and PP3');
      expect(within(variant).queryByText('BP4')).not.toBeInTheDocument();
      expect(variant).toHaveTextContent('Evidence: Functional assay shows loss of function.');
      expect(variant).toHaveTextContent('ClinVar reported Likely pathogenic, with annotation version v1');
      expect(variant).toHaveTextContent('Interpretation as signed.');
      expect(variant).toHaveTextContent('Tags: acmg_class_4, report');

      const sv = screen.getByRole('heading', { name: 'Structural variant 2-1000-50000-DEL---' }).closest('article')!;
      expect(sv).toHaveTextContent('Pathogenic - class 5 · 1 pts');
      expect(sv).toHaveTextContent('2A1 pts');
      expect(sv).toHaveTextContent('Evidence: Covers the HI region.');

      // The checks frozen at sign-out.
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent('Overall: Pass (Trio WGS).');
      expect(checks).toHaveTextContent('Sex of PROBAND: Pass — Recorded and inferred sex agree.');
      expect(checks).toHaveTextContent('PROBAND: Warning — Mean coverage 18 × (Warning; warning 20, fail 10)');
      expect(checks).toHaveTextContent('The family’s data had imported completely.');
      expect(screen.getByText(/No reported classification had changed evidence/)).toHaveTextContent(
        'No reported classification had changed evidence when this version was signed (1 checked).',
      );
      // The record holds the SV/CNV drift check too, so no note says it is missing.
      expect(screen.queryByText(/holds no drift check of the structural-variant/)).not.toBeInTheDocument();

      // The footer: the versions and the build as signed, and the build that rendered it.
      const footer = container.querySelector('footer')!;
      expect(footer).toHaveTextContent(
        'Modules & versions as signed: Reference assembly GRCh38 (2013-12-01) · ClinVar 2026-04',
      );
      expect(footer).toHaveTextContent('Signed with: CoGA 0.1.0 (abc1234)');
      expect(await within(footer).findByText('CoGA 0.2.0 (0123456)')).toBeInTheDocument();
      expect(footer).toHaveTextContent('Rendered by: CoGA 0.2.0 (0123456)');
      expect(footer).toHaveTextContent('In-house IVD per IVDR Article 5(5)');

      // Nothing on it comes from the family's current data: none of it is asked for.
      expect(screen.queryByText(/BRCA1/)).not.toBeInTheDocument();
      expect(screen.queryByText(/tumour suppressor/)).not.toBeInTheDocument();
      expect(screen.queryByText(/Strong candidate/)).not.toBeInTheDocument();
      expect(screen.queryByText(/2026-05/)).not.toBeInTheDocument();
      const requested = apiMock.get.mock.calls.map(([url]) => String(url));
      LIVE_ONLY_REQUESTS.forEach((live) =>
        expect(requested.filter((url) => url.startsWith(live))).toEqual([]),
      );
      // The latest intact signed version prints without a notice.
      expect(container.querySelector('.report-print-notice')).toBeNull();
    });

    it('says what the signed record does not hold, for the report and for each variant', async () => {
      mockSignedCase();
      renderPage();

      const note = (await screen.findByRole('heading', { name: 'Not in the signed record' })).closest(
        'section',
      )!;
      expect(note).toHaveTextContent(
        'the variant description: gene, HGVS, consequence, genotypes, population frequency and in silico predictions',
      );
      expect(note).toHaveTextContent('the segregation in the family, and the family’s members');
      expect(note).toHaveTextContent('the gene description, its associated conditions and its gene panels');
      expect(note).toHaveTextContent('the phenotype (HPO) terms and their overlap with the gene');
      expect(note).toHaveTextContent('the classification audit trail and the analysis pipeline settings');
      // Said on each variant card, which prints on its own.
      expect(
        screen.getAllByText('Not in the signed record: the variant description, segregation, gene and phenotype.'),
      ).toHaveLength(2);
    });

    it('prints the signed version: the page Print prints is the record', async () => {
      mockSignedCase();
      const print = vi.spyOn(window, 'print').mockImplementation(() => undefined);
      const { container } = renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Print signed version 2' }));
      expect(print).toHaveBeenCalledTimes(1);
      // What prints is the record: its variant, not the live report's.
      expect(screen.getByRole('heading', { name: '17-43000000-A-G' })).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /BRCA1/ })).not.toBeInTheDocument();
      // The actions and the comparison with today's data stay off the printout.
      expect(screen.getByRole('button', { name: 'Print signed version 2' }).closest('.no-print')).not.toBeNull();
      expect(
        (await screen.findByText('The family’s current data still matches this signed version.')).closest('.no-print'),
      ).not.toBeNull();
      expect(container.querySelector('.report-print-notice')).toBeNull();
      print.mockRestore();
    });

    it('says when a record holds the small-variant drift check but not the SV/CNV one', async () => {
      const withoutStructuralDrift: Record<string, unknown> = { ...SIGNED_SNAPSHOT };
      delete withoutStructuralDrift.structural_drift;
      mockSignedCase({ snapshot: withoutStructuralDrift });
      renderPage();

      await signedRecordCard();
      expect(
        screen.getByText(/The signed record holds no drift check of the structural-variant and CNV classifications/),
      ).toBeInTheDocument();
    });

    it('says what a record that does not hold a section lacks', async () => {
      const older = {
        family_id: 'F1',
        assembly: 'GRCh38',
        modules: [{ key: 'clinvar', label: 'ClinVar', version: '2025-01' }],
        reported_variants: SIGNED_SNAPSHOT.reported_variants,
      };
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, software_version: null, git_sha: null, qc_status: null }],
        snapshot: older,
      });
      const { container } = renderPage();

      const card = await signedRecordCard();
      expect(card).toHaveTextContent('Signed with not in the signed record');
      // A section the record does not hold reads as not in the record, never as "none".
      expect(screen.getByText(/no list of reported structural variants/)).toHaveTextContent(
        'Signed version 2 records 1 reported small variant and no list of reported structural variants in family F1.',
      );
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(within(checks).getAllByText('Not in the signed record.')).toHaveLength(3);
      const drift = screen.getByRole('heading', { name: 'Evidence drift at sign-out' }).closest('section')!;
      expect(drift).toHaveTextContent('Not in the signed record.');
      expect(container.querySelector('footer')).toHaveTextContent('Signed with: not in the signed record');
    });

    it('names what the signed record could not capture (#514)', async () => {
      mockSignedCase({
        detail: (version) =>
          Promise.resolve({
            data: {
              ...SIGNED_ENTRY,
              verified: true,
              snapshot: { ...SIGNED_SNAPSHOT, version },
              not_captured: [
                { section: 'sequencing_qc', item: 'Sequencing-QC cut-offs', reason: 'QC thresholds could not be resolved' },
                { section: 'modules', item: 'Monarch', reason: 'lookup failed' },
              ],
            },
          }),
      });
      renderPage();

      const note = await screen.findByText(/Not captured in signed version 2:/);
      expect(note.closest('p')?.textContent).toMatch(
        /Sequencing-QC cut-offs \(QC thresholds could not be resolved\); Monarch \(lookup failed\)\./,
      );
    });

    it('shows no capture note for a complete signed record', async () => {
      mockSignedCase();
      renderPage();

      await signedRecordCard();
      expect(screen.queryByText(/Not captured in signed version/)).not.toBeInTheDocument();
    });

    it('counts and lists the SV/CNV classifications whose evidence had moved at sign-out', async () => {
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, drift_acknowledged: true, drift_acknowledgement_reason: 'SV re-reviewed' }],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          structural_drift: {
            checked: 2,
            drifted_count: 1,
            drifted: [
              {
                variant_id: 'sv-18-del',
                status: 'drifted',
                classified_by: 'bob',
                changed: ['pli'],
                evidence_from: { chrom: '18', start: 100, end: 400, pli: 0.99 },
                evidence_to: { chrom: '18', start: 100, end: 400, pli: 0.41 },
              },
            ],
          },
          acknowledged_drift: true,
          drift_acknowledgement_reason: 'SV re-reviewed',
        },
      });
      renderPage();

      await signedRecordCard();
      const drift = screen.getByRole('heading', { name: 'Evidence drift at sign-out' }).closest('section')!;
      // Only an SV/CNV classification drifted: the record must not read as if none had.
      expect(drift).not.toHaveTextContent('No reported classification had changed evidence');
      expect(drift).toHaveTextContent(
        '1 classification had evidence that changed, or could not be verified, when this version was signed',
      );
      expect(drift).toHaveTextContent('sv-18-del (structural variant) — pLI 0.990 → 0.410 (classified by bob)');
      expect(drift).toHaveTextContent('Signed out over it, with the reason: SV re-reviewed.');
      expect(drift).not.toHaveTextContent(/The record predates/);
    });

    it('shows the overrides frozen into the signed version', async () => {
      const entry = {
        ...SIGNED_ENTRY,
        qc_status: 'fail',
        qc_acknowledged: true,
        qc_acknowledgement_reason: 'Repeat genotyping confirms identity',
        drift_acknowledged: true,
        drift_acknowledgement_reason: 'reviewed',
      };
      mockSignedCase({
        versions: [entry],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          sample_qc: { ...SIGNED_SNAPSHOT.sample_qc, overall_status: 'fail' },
          acknowledged_qc: true,
          qc_acknowledgement_reason: 'Repeat genotyping confirms identity',
          drift: {
            checked: 1,
            drifted_count: 1,
            drifted: [{ variant_id: '17-43000000-A-G', status: 'drifted', clinvar_from: 'Uncertain_significance', clinvar_to: 'Likely_pathogenic', classified_by: 'alice' }],
          },
          acknowledged_drift: true,
          drift_acknowledgement_reason: 'reviewed',
        },
      });
      renderPage();

      const card = await signedRecordCard();
      expect(within(card).getByText('Sample QC').closest('p')).toHaveTextContent(
        'Sample QC Fail — override acknowledged: Repeat genotyping confirms identity',
      );
      expect(within(card).getByText('Evidence drift').closest('p')).toHaveTextContent(
        'Evidence drift override acknowledged: reviewed',
      );
      const drift = screen.getByRole('heading', { name: 'Evidence drift at sign-out' }).closest('section')!;
      expect(drift).toHaveTextContent(
        '1 classification had evidence that changed, or could not be verified, when this version was signed',
      );
      expect(drift).toHaveTextContent('17-43000000-A-G — ClinVar Uncertain_significance → Likely_pathogenic (classified by alice)');
      expect(drift).toHaveTextContent('Signed out over it, with the reason: reviewed.');
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent('Overall: Fail (Trio WGS).');
      expect(checks).toHaveTextContent('Signed out over it, with the reason: Repeat genotyping confirms identity.');
    });

    it('says a pre-binding or unstamped build as the record holds it', async () => {
      mockSignedCase({ versions: [{ ...SIGNED_ENTRY, software_version: '0.0.0+unknown', git_sha: 'unknown' }] });
      renderPage();

      // An unstamped build shows the version but suppresses the "(unknown)" parens.
      const card = await signedRecordCard();
      expect(card).toHaveTextContent('Signed with CoGA 0.0.0+unknown');
      expect(screen.queryByText(/unknown\)/)).not.toBeInTheDocument();
    });

    it('marks a record that fails its content hash, on screen and in print', async () => {
      mockSignedCase({
        detail: (version) =>
          Promise.resolve({
            data: { ...SIGNED_ENTRY, verified: false, snapshot: { ...SIGNED_SNAPSHOT, version }, not_captured: [] },
          }),
      });
      const { container } = renderPage();

      expect(await screen.findByRole('alert')).toHaveTextContent(
        '⚠ The stored record does not match its content hash. It may have been changed after it was signed. Do not use it as the signed report, and report it.',
      );
      expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
        /^Do not use — the stored record of signed version 2 does not match its content hash\./,
      );
    });

    it('says a superseded version is superseded, and opens the others', async () => {
      mockSignedCase({ versions: [SIGNED_ENTRY, V1_ENTRY] });
      const { container } = renderPage(undefined, '/families/F1/report?version=1');

      const card = await signedRecordCard(1);
      expect(card).toHaveTextContent('Signed version 1 — signed out by alice on 2026-06-20 09:00 UTC');
      expect(within(card).getByText('Superseded.').closest('p')).toHaveTextContent(
        'Superseded. Signed version 2, signed out by bjorn on 2026-06-25 10:00 UTC, is the latest.',
      );
      expect(container.querySelector('.report-print-notice')?.textContent).toBe(
        'Superseded — signed version 2 replaces signed version 1.',
      );
      // Only the latest version is compared with the family's current data.
      expect(apiMock.get).not.toHaveBeenCalledWith('/families/F1/report/sign-out-check');

      const nav = screen.getByRole('navigation', { name: 'Signed versions' });
      expect(nav).toHaveTextContent('Signed versions version 2 (latest) · version 1');
      fireEvent.click(within(nav).getByRole('link', { name: 'version 2' }));
      expect(await signedRecordCard(2)).toHaveTextContent('This is the latest signed version.');
    });

    it('says when the family’s data changed since the latest signed version, off the printout', async () => {
      mockSignedCase({
        check: () => checkResult({ matches: false, changed_sections: ['reported_variants', 'import_incomplete'] }),
      });
      const { container } = renderPage();

      const line = (await screen.findByText(/Changed since this version was signed:/)).closest('p')!;
      expect(line).toHaveTextContent(
        '⚠ Changed since this version was signed: reported small variants and import completeness. This page still shows version 2 as it was signed.',
      );
      expect(line).toHaveClass('no-print');
      // The version is still what was signed: its printout carries no notice.
      expect(container.querySelector('.report-print-notice')).toBeNull();
    });

    it('says when that comparison could not be made', async () => {
      mockSignedCase({ check: () => Promise.reject({ response: { status: 500, data: {} } }) });
      renderPage();

      expect(
        await screen.findByText('Whether the family’s current data still matches this version could not be checked.'),
      ).toBeInTheDocument();
    });

    it('downloads the signed version it shows', async () => {
      mockSignedCase();
      const createObjectURL = vi.fn(() => 'blob:signed');
      const revokeObjectURL = vi.fn();
      Object.assign(URL, { createObjectURL, revokeObjectURL });
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Download signed version 2 (JSON)' }));
      await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
      expect(apiMock.get).toHaveBeenCalledWith('/families/F1/report/sign-outs/2');
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:signed');
      expect(clickSpy).toHaveBeenCalledTimes(1);
      clickSpy.mockRestore();
    });

    it('shows nothing in place of a signed version that cannot be loaded', async () => {
      let failing = true;
      mockSignedCase({
        detail: (version) =>
          failing
            ? Promise.reject({ response: { status: 500, data: {} } })
            : Promise.resolve({
                data: { ...SIGNED_ENTRY, verified: true, snapshot: { ...SIGNED_SNAPSHOT, version }, not_captured: [] },
              }),
      });
      renderPage();

      expect(await screen.findByText('Signed version 2 could not be loaded')).toBeInTheDocument();
      expect(
        screen.getByText('The signed record could not be retrieved or read, so it is not shown. Nothing is shown in its place.'),
      ).toBeInTheDocument();
      expect(screen.queryByText(/BRCA1/)).not.toBeInTheDocument();
      failing = false;
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await signedRecordCard()).toBeInTheDocument();
    });

    it('says there is no such signed version', async () => {
      mockSignedCase();
      renderPage(undefined, '/families/F1/report?version=7');

      expect(await screen.findByText('There is no signed version 7')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'View signed version 2' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    });

    it('does not take an unreadable version number for a signed version', async () => {
      mockSignedCase();
      renderPage(undefined, '/families/F1/report?version=abc');

      expect(await screen.findByText('Not a signed version')).toBeInTheDocument();
      expect(screen.getByText('“abc” is not the number of a signed version.')).toBeInTheDocument();
    });
  });

  describe('the live report of a signed case', () => {
    it('is labelled as not the signed version, even when its content matches', async () => {
      mockSignedCase();
      const { container } = renderPage(undefined, '/families/F1/report?view=live');

      // Live data: the variant description and gene context the signed record does not hold.
      expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();
      expect(await screen.findByText('This is the live report, not signed version 2.')).toBeInTheDocument();
      expect(await screen.findByText(/This page still matches signed version 2\./)).toBeInTheDocument();
      expect(screen.getByText('Clinical report — live, not the signed version')).toBeInTheDocument();
      expect(screen.queryByText(/✓/)).not.toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).toBe(
        'Not the signed report — this is the live report. Print signed version 2 from its record.',
      );
      // Signing out again is done from here.
      expect(screen.getByRole('button', { name: /Amend sign-out/ })).toBeEnabled();

      // The signed version is one click away.
      fireEvent.click(screen.getByRole('link', { name: 'View signed version 2' }));
      expect(await signedRecordCard()).toBeInTheDocument();
      expect(screen.queryByRole('heading', { name: /BRCA1/ })).not.toBeInTheDocument();
    });

    it('is opened from the signed version', async () => {
      mockSignedCase();
      renderPage();

      await signedRecordCard();
      fireEvent.click(screen.getAllByRole('link', { name: 'Open the live report' })[0]);
      expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();
      expect(await screen.findByText('This is the live report, not signed version 2.')).toBeInTheDocument();
    });

    it('warns — on screen and in print — when the content changed after sign-out', async () => {
      mockSignedCase({
        check: () => checkResult({ matches: false, changed_sections: ['reported_variants', 'sequencing_qc'] }),
      });
      const { container } = renderPage(undefined, '/families/F1/report?view=live');

      expect(
        await screen.findByText(/reported small variants and sequencing QC cut-offs/),
      ).toBeInTheDocument();
      expect(screen.getByText(/Changed since sign-out/)).toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
        /Not the signed report — the content differs from signed version 2/,
      );
    });

    it('treats the page as unsigned when the check cannot be made', async () => {
      mockSignedCase({ check: () => Promise.reject({ response: { status: 500, data: {} } }) });
      const { container } = renderPage(undefined, '/families/F1/report?view=live');

      expect(
        await screen.findByText(/could not be checked against signed version 2 — treat it as unsigned/),
      ).toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).toMatch(/Not verified/);
    });

    it('downloads the frozen signed version', async () => {
      mockSignedCase();
      const createObjectURL = vi.fn(() => 'blob:signed');
      const revokeObjectURL = vi.fn();
      Object.assign(URL, { createObjectURL, revokeObjectURL });
      // jsdom cannot navigate to a blob: URL; the click itself is what we need.
      const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      renderPage(undefined, '/families/F1/report?view=live');

      fireEvent.click(await screen.findByRole('button', { name: /Download signed version 2/ }));
      await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
      expect(apiMock.get).toHaveBeenCalledWith('/families/F1/report/sign-outs/2');
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:signed');
      expect(clickSpy).toHaveBeenCalledTimes(1);
      clickSpy.mockRestore();
    });

    it('is neither a draft nor signable while the sign-out record loads', async () => {
      mockApi();
      const base = apiMock.get.getMockImplementation()!;
      apiMock.get.mockImplementation((url: string, config?: unknown) =>
        url === '/families/F1/report/sign-outs' ? new Promise(() => undefined) : base(url, config),
      );
      const { container } = renderPage(undefined, '/families/F1/report?view=live');

      expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();
      expect(screen.getByText('Clinical report — live')).toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
        /The sign-out record is still loading — do not use as the signed report\./,
      );
      expect(screen.queryByText(/Draft — this report has not been signed/)).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: /Sign out report/ })).toBeDisabled();
    });
  });

  it('shows the signer the version just signed, from its record', async () => {
    let signed = false;
    const V1_SIGNED = { ...SIGNED_ENTRY, version: 1 };
    mockApi();
    const base = apiMock.get.getMockImplementation()!;
    apiMock.get.mockImplementation((url: string, config?: unknown) => {
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: { family_id: 'F1', latest: signed ? V1_SIGNED : null, signouts: signed ? [V1_SIGNED] : [] },
        });
      }
      if (url === '/families/F1/report/sign-outs/1') {
        return Promise.resolve({
          data: { ...V1_SIGNED, verified: true, snapshot: { ...SIGNED_SNAPSHOT, version: 1 }, not_captured: [] },
        });
      }
      if (url === '/families/F1/report/sign-out-check') return checkResult({ version: 1, matches: true });
      return base(url, config);
    });
    apiMock.post.mockImplementation(() => {
      signed = true;
      return Promise.resolve({ data: { ...V1_SIGNED, snapshot: SIGNED_SNAPSHOT, not_captured: [] } });
    });
    renderPage();

    // A case never signed out opens on the live report, a draft.
    expect(await screen.findByText('Clinical report — draft, not signed')).toBeInTheDocument();
    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));

    expect(await signedRecordCard(1)).toHaveTextContent('Signed version 1 — signed out by bjorn on 2026-06-25 10:00 UTC');
    expect(await screen.findByText('The family’s current data still matches this signed version.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /BRCA1/ })).not.toBeInTheDocument();
  });

  it('marks an unsigned report as a draft in print', async () => {
    mockUnsignedFamily();
    const { container } = renderPage();

    await screen.findByRole('button', { name: /Sign out report/ });
    expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
      /Draft — this report has not been signed/,
    );
  });

  const DRIFT_409 = {
    response: {
      status: 409,
      data: { detail: '1 classification(s) have evidence that changed since they were made.' },
    },
  };

  it('requires a reason to override the evidence-drift gate (no bare confirm)', async () => {
    mockUnsignedFamily();
    const confirmSpy = vi.spyOn(window, 'confirm');
    const posts: Array<Record<string, unknown>> = [];
    apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
      posts.push(body);
      return posts.length === 1
        ? Promise.reject(DRIFT_409)
        : Promise.resolve({ data: { version: 1 } });
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
    expect(
      await screen.findByRole('dialog', { name: /Evidence drift acknowledgement required/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/evidence that changed since they were made/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Sign out anyway/ })).toBeDisabled();

    fireEvent.change(screen.getByLabelText(/despite the evidence drift/), {
      target: { value: 'ClinVar update reviewed; classification unchanged' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));

    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1]).toMatchObject({
      acknowledge_drift: true,
      drift_acknowledgement_reason: 'ClinVar update reviewed; classification unchanged',
      acknowledge_qc: false,
    });
    expect(confirmSpy).not.toHaveBeenCalled();
    confirmSpy.mockRestore();
  });

  it('carries the drift reason through a following Sample-QC override', async () => {
    mockUnsignedFamily();
    const posts: Array<Record<string, unknown>> = [];
    apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
      posts.push(body);
      if (posts.length === 1) return Promise.reject(DRIFT_409);
      if (posts.length === 2) return Promise.reject(QC_409);
      return Promise.resolve({ data: { version: 1 } });
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
    fireEvent.change(await screen.findByLabelText(/despite the evidence drift/), {
      target: { value: 'drift reviewed' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));

    fireEvent.change(await screen.findByLabelText(/despite the QC concern/), {
      target: { value: 'identity confirmed' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));

    await waitFor(() => expect(posts).toHaveLength(3));
    expect(posts[2]).toMatchObject({
      acknowledge_drift: true,
      drift_acknowledgement_reason: 'drift reviewed',
      acknowledge_qc: true,
      qc_acknowledgement_reason: 'identity confirmed',
    });
  });

  it('shows a sign-out failure instead of failing silently', async () => {
    mockUnsignedFamily();
    apiMock.post.mockRejectedValue({
      response: { status: 500, data: { detail: 'database unavailable' } },
    });
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/Not signed out\. database unavailable/);
  });

  // A family-package import that partly failed leaves the family flagged import_incomplete.
  // The report warns while it is set, and sign-out needs it acknowledged with a reason, as
  // a Sample-QC concern does.
  describe('when the family’s import is incomplete', () => {
    const IMPORT_JOB_ID = '3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b';
    const IMPORT_FLAG = {
      at: '2026-09-12T10:14:00+00:00',
      failed_datasets: ['snv', 'sv'],
      imported_datasets: ['coverage'],
      job_id: IMPORT_JOB_ID,
    };
    const IMPORT_409 = {
      response: {
        status: 409,
        data: {
          detail: {
            gate: 'import_incomplete',
            message:
              "This family's data is incomplete: the package import (2026-09-12T10:14:00+00:00) failed for snv, sv (coverage did import). Re-run the import to complete it, or acknowledge with a reason to sign out anyway.",
            import_incomplete: IMPORT_FLAG,
          },
        },
      },
    };
    const REASON = 'SV calls are not part of this referral; SNV re-import booked.';

    function mockIncompleteFamily() {
      mockUnsignedFamily();
      const base = apiMock.get.getMockImplementation()!;
      apiMock.get.mockImplementation((url: string, config?: unknown) =>
        url === '/families/F1'
          ? Promise.resolve({
              data: {
                family_id: 'F1',
                members: [],
                projects: [],
                metadata: { import_incomplete: IMPORT_FLAG },
              },
            })
          : base(url, config),
      );
    }

    it('warns on the report while the flag is set, naming what failed', async () => {
      mockIncompleteFamily();
      renderPage();

      const banner = (await screen.findByText(/Import incomplete\./)).closest('[role="alert"]');
      expect(banner).toHaveTextContent(/failed for snv and sv; coverage did import/);
      // In the header card, which prints with the report.
      expect(banner?.closest('.report-header')).not.toBeNull();
      expect(banner?.closest('.no-print')).toBeNull();
    });

    it('requires a reason to sign out, and posts the acknowledgement', async () => {
      mockIncompleteFamily();
      const posts: Array<Record<string, unknown>> = [];
      apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
        posts.push(body);
        return posts.length === 1
          ? Promise.reject(IMPORT_409)
          : Promise.resolve({ data: { version: 1 } });
      });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      const dialog = await screen.findByRole('dialog', {
        name: /Incomplete import acknowledgement required/i,
      });
      // The server's message verbatim, and what failed and what did import.
      expect(within(dialog).getByText(/failed for snv, sv \(coverage did import\)/)).toBeInTheDocument();
      expect(within(dialog).getByText('Failed to import: snv, sv')).toBeInTheDocument();
      expect(within(dialog).getByText('Imported: coverage')).toBeInTheDocument();
      // The job whose record holds each dataset's error.
      expect(within(dialog).getByText(`Import job: ${IMPORT_JOB_ID}`)).toBeInTheDocument();

      const confirm = within(dialog).getByRole('button', { name: /Sign out anyway/ });
      expect(confirm).toBeDisabled();
      fireEvent.change(within(dialog).getByLabelText(/despite the incomplete import/), {
        target: { value: `  ${REASON}  ` },
      });
      expect(confirm).toBeEnabled();
      fireEvent.click(confirm);

      await waitFor(() => expect(posts).toHaveLength(2));
      expect(posts[0]).toMatchObject({ acknowledge_import_incomplete: false });
      expect(posts[1]).toMatchObject({
        acknowledge_import_incomplete: true,
        import_incomplete_acknowledgement_reason: REASON,
      });
      await waitFor(() =>
        expect(
          screen.queryByRole('dialog', { name: /Incomplete import acknowledgement required/i }),
        ).not.toBeInTheDocument(),
      );
    });

    it('carries the drift and QC reasons through to the incomplete-import override', async () => {
      mockIncompleteFamily();
      const posts: Array<Record<string, unknown>> = [];
      apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
        posts.push(body);
        if (posts.length === 1) return Promise.reject(DRIFT_409);
        if (posts.length === 2) return Promise.reject(QC_409);
        if (posts.length === 3) return Promise.reject(IMPORT_409);
        return Promise.resolve({ data: { version: 1 } });
      });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      fireEvent.change(await screen.findByLabelText(/despite the evidence drift/), {
        target: { value: 'drift reviewed' },
      });
      fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));
      fireEvent.change(await screen.findByLabelText(/despite the QC concern/), {
        target: { value: 'identity confirmed' },
      });
      fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));
      fireEvent.change(await screen.findByLabelText(/despite the incomplete import/), {
        target: { value: REASON },
      });
      fireEvent.click(screen.getByRole('button', { name: /Sign out anyway/ }));

      await waitFor(() => expect(posts).toHaveLength(4));
      expect(posts[3]).toMatchObject({
        acknowledge_drift: true,
        drift_acknowledgement_reason: 'drift reviewed',
        acknowledge_qc: true,
        qc_acknowledgement_reason: 'identity confirmed',
        acknowledge_import_incomplete: true,
        import_incomplete_acknowledgement_reason: REASON,
      });
    });

    it('does not sign out when the override is cancelled', async () => {
      mockIncompleteFamily();
      const posts: Array<Record<string, unknown>> = [];
      apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
        posts.push(body);
        return Promise.reject(IMPORT_409);
      });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      const dialog = await screen.findByRole('dialog', {
        name: /Incomplete import acknowledgement required/i,
      });
      fireEvent.click(within(dialog).getByRole('button', { name: /Cancel/ }));

      await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
      expect(posts).toHaveLength(1);
    });


    it('shows the frozen incomplete-import override on the signed record', async () => {
      const entry = {
        ...SIGNED_ENTRY,
        import_incomplete_failed_datasets: ['snv', 'sv'],
        import_incomplete_job_id: IMPORT_JOB_ID,
        import_incomplete_acknowledged: true,
        import_incomplete_acknowledgement_reason: REASON,
      };
      mockSignedCase({
        versions: [entry],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          import_incomplete: IMPORT_FLAG,
          acknowledged_import_incomplete: true,
          import_incomplete_acknowledgement_reason: REASON,
        },
      });
      renderPage();

      const card = await signedRecordCard();
      expect(within(card).getByText('Incomplete import').closest('p')).toHaveTextContent(
        `Incomplete import snv and sv not imported (import job ${IMPORT_JOB_ID}) — override acknowledged: ${REASON}`,
      );
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent(
        `The family’s data was incomplete: snv and sv failed to import; coverage did import (import of 2026-09-12 10:14 UTC). Import job ${IMPORT_JOB_ID}. Signed out over it, with the reason: ${REASON}`,
      );
    });

    it('names a change in import completeness since sign-out', async () => {
      mockSignedCase({ check: () => checkResult({ matches: false, changed_sections: ['import_incomplete'] }) });
      renderPage(undefined, '/families/F1/report?view=live');

      expect(await screen.findByText(/Changed since sign-out/)).toBeInTheDocument();
      expect(screen.getByText(/import completeness\. This page shows the current state/)).toBeInTheDocument();
    });

    it('names a change in the structural variants’ evidence drift since sign-out', async () => {
      mockSignedCase({ check: () => checkResult({ matches: false, changed_sections: ['structural_drift'] }) });
      renderPage(undefined, '/families/F1/report?view=live');

      expect(await screen.findByText(/Changed since sign-out/)).toBeInTheDocument();
      expect(
        screen.getByText(/structural-variant evidence drift\. This page shows the current state/),
      ).toBeInTheDocument();
    });

  });

  // #605 — a failed request is never printed as an empty or complete report.
  describe('when a request fails', () => {
    const serverError = () => Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));

    /**
     * The full report of `mockApi`, with the listed URLs failing until `recover` is called:
     * a failure that lasts, as a new observer mounting would otherwise retry it away.
     */
    const mockApiFailing = (...urls: string[]) => {
      mockApi();
      const base = apiMock.get.getMockImplementation()!;
      let failing = true;
      apiMock.get.mockImplementation((url: string, config?: unknown) =>
        failing && urls.some((candidate) => url.startsWith(candidate)) ? serverError() : base(url, config),
      );
      return {
        recover: () => {
          failing = false;
        },
      };
    };

    it('does not render an empty report when the family could not be loaded', async () => {
      const api = mockApiFailing('/families/F1');
      renderPage();

      expect(await screen.findByText('Report could not be loaded')).toBeInTheDocument();
      expect(screen.getByText(/This is not a report without variants/)).toBeInTheDocument();
      expect(screen.queryByText(/This report summarises/)).not.toBeInTheDocument();

      api.recover();
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();
    });

    it('does not drop the reported SVs silently', async () => {
      mockApiFailing('/families/F1/structural-variants');
      renderPage();

      expect(await screen.findByText('Report could not be loaded')).toBeInTheDocument();
      expect(
        screen.getByText(/The reported structural variants for this family could not be retrieved/),
      ).toBeInTheDocument();
      expect(screen.queryByText(/This report summarises/)).not.toBeInTheDocument();
    });

    it('does not call a case a draft when its sign-out record could not be loaded', async () => {
      mockApiFailing('/families/F1/report/sign-outs');
      renderPage();

      expect(await screen.findByText(/The sign-out record could not be loaded,/)).toBeInTheDocument();
      expect(
        screen.getByText(
          'The sign-out record could not be loaded — do not use as the signed report.',
        ),
      ).toBeInTheDocument();
      expect(screen.queryByText(/Draft — this report has not been signed/)).not.toBeInTheDocument();
      // It may already be signed: sign-out is not offered until the record is known.
      expect(screen.getByRole('button', { name: /Sign out report/ })).toBeDisabled();
    });

    it('marks each part that could not be loaded, on screen and in print, and retries them', async () => {
      const api = mockApiFailing(
        '/genes/profile',
        '/families/F1/hpo',
        '/families/F1/classification-drift',
        '/families/F1/clinical-audit',
        '/families/F1/annotation-manifest',
      );
      renderPage();

      // Printed at the top of every page, as the sign-out notices are.
      await waitFor(() =>
        expect(screen.getByText(/so this printout does not show the whole report/)).toHaveTextContent(
          'Incomplete — the description of BRCA1, the family’s HPO terms, the evidence-drift check, the audit trail and the annotation provenance could not be loaded',
        ),
      );
      expect(
        screen.getByText(
          (_content, element) =>
            element?.tagName === 'P' && element.textContent === 'The description of BRCA1 could not be loaded.',
        ),
      ).toBeInTheDocument();
      expect(screen.getByText(/The family’s HPO terms could not be loaded/)).toBeInTheDocument();
      expect(screen.getByText('⚠ Evidence drift could not be checked')).toBeInTheDocument();
      expect(screen.getByText('The audit trail could not be loaded.')).toBeInTheDocument();
      expect(screen.getByText(/Modules & versions:/).parentElement).toHaveTextContent('could not be loaded');
      // None of them reads as a finding of "none".
      expect(screen.queryByText(/No curated description is available/)).not.toBeInTheDocument();
      expect(screen.queryByText(/No HPO phenotype terms have been recorded/)).not.toBeInTheDocument();
      expect(screen.queryByText(/not recorded for this family/)).not.toBeInTheDocument();

      api.recover();
      fireEvent.click(
        within(screen.getByText(/Parts of this report could not be loaded/).closest('section')!).getByRole(
          'button',
          { name: 'Retry' },
        ),
      );

      expect(await screen.findByText(/tumour suppressor/)).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.queryByText(/Parts of this report could not be loaded/)).not.toBeInTheDocument(),
      );
      expect(screen.getByText(/ClinVar 2026-05/)).toBeInTheDocument();
    });

    it('says the software version could not be loaded, on screen and in print, and retries it', async () => {
      mockApi();
      let failing = true;
      apiMock.version.mockImplementation(() =>
        failing ? serverError() : Promise.resolve({ data: RUNNING_BUILD }),
      );
      renderPage();

      const footer = await reportFooter();
      expect(
        await within(footer).findByText('CoGA — the version could not be loaded'),
      ).toBeInTheDocument();
      // A printout that cannot name the build that produced it is not complete.
      expect(screen.getByText(/so this printout does not show the whole report/)).toHaveTextContent(
        'Incomplete — the software version could not be loaded',
      );

      failing = false;
      fireEvent.click(
        within(screen.getByText(/Parts of this report could not be loaded/).closest('section')!).getByRole(
          'button',
          { name: 'Retry' },
        ),
      );

      expect(await within(footer).findByText('CoGA 0.2.0 (0123456)')).toBeInTheDocument();
      await waitFor(() =>
        expect(screen.queryByText(/Parts of this report could not be loaded/)).not.toBeInTheDocument(),
      );
    });
  });
});
