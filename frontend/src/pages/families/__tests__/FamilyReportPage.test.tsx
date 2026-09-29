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

const renderPage = (queryClient = createTestQueryClient()) =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/families/F1/report']}>
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

  it('shows the frozen sign-out record when the case is signed out', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            latest: {
              version: 2,
              signed_out_by: 'bjorn',
              signed_out_at: '2026-06-25T10:00:00Z',
              content_hash: 'abc123def456',
              software_version: '0.1.0',
              git_sha: 'abc1234def567',
            },
            signouts: [],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/Signed out — version 2 by/)).toBeInTheDocument();
    expect(screen.getByText(/abc123def456/)).toBeInTheDocument();
    // The frozen software identity ("as signed") is shown: version + short git sha.
    expect(screen.getByText(/CoGA 0\.1\.0 \(abc1234\)/)).toBeInTheDocument();
    // Once signed out, the action becomes an amendment.
    expect(screen.getByRole('button', { name: /Amend sign-out/ })).toBeInTheDocument();
  });

  it('hides the Software line for a pre-binding sign-out (backfill-safe)', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            latest: {
              version: 1,
              signed_out_by: 'bjorn',
              signed_out_at: '2026-06-25T10:00:00Z',
              content_hash: 'oldhash',
              software_version: null,
              git_sha: null,
            },
            signouts: [],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/Signed out — version 1 by/)).toBeInTheDocument();
    // Older sign-outs predate version-binding: no Software line, no crash.
    expect(screen.queryByText('Software')).not.toBeInTheDocument();
  });

  it('omits the git-sha parens when the build identity is unknown', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            latest: {
              version: 1,
              signed_out_by: 'bjorn',
              signed_out_at: '2026-06-25T10:00:00Z',
              content_hash: 'h',
              software_version: '0.0.0+unknown',
              git_sha: 'unknown',
            },
            signouts: [],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    // An unstamped build shows the version but suppresses the "(unknown)" parens.
    expect(await screen.findByText('CoGA 0.0.0+unknown')).toBeInTheDocument();
    expect(screen.queryByText(/unknown\)/)).not.toBeInTheDocument();
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

  it('renders the frozen Sample QC status + override reason in the signed-out record', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            latest: {
              version: 2,
              signed_out_by: 'bjorn',
              signed_out_at: '2026-06-25T10:00:00Z',
              content_hash: 'abc123',
              // Scalar fields the list endpoint actually returns (extracted from the
              // frozen JSONB snapshot), not a nested `snapshot` object.
              qc_status: 'fail',
              qc_acknowledged: true,
              qc_acknowledgement_reason: 'Repeat genotyping confirms identity',
            },
            signouts: [],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/Signed out — version 2/)).toBeInTheDocument();
    expect(screen.getByText('Sample QC')).toBeInTheDocument();
    expect(
      screen.getByText(/override acknowledged: Repeat genotyping confirms identity/),
    ).toBeInTheDocument();
  });

  // #508 — the report page must only present itself as the signed record when its live
  // content still matches the latest sign-out.
  const SIGNED_LATEST = {
    version: 2,
    signed_out_by: 'bjorn',
    signed_out_at: '2026-06-25T10:00:00Z',
    content_hash: 'abc123def456',
  };

  function mockSignedFamily(check: () => Promise<unknown>) {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
      }
      if (url.startsWith('/families/F1/small-variants')) {
        return Promise.resolve({ data: { variants: [], total: 0 } });
      }
      if (url === '/families/F1/report/sign-outs') {
        return Promise.resolve({ data: { family_id: 'F1', latest: SIGNED_LATEST, signouts: [] } });
      }
      if (url === '/families/F1/report/sign-out-check') {
        return check();
      }
      if (url === '/families/F1/report/sign-outs/2') {
        return Promise.resolve({ data: { version: 2, snapshot: { reported_variants: [] } } });
      }
      return Promise.resolve({ data: [] });
    });
  }

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

  it('presents the page as signed only when it matches the latest sign-out', async () => {
    mockSignedFamily(() => checkResult({ matches: true }));
    const { container } = renderPage();

    expect(await screen.findByText(/This page matches signed version 2/)).toBeInTheDocument();
    expect(screen.getByText(/✓ Signed out — version 2 by/)).toBeInTheDocument();
    // A verified match prints without any "not the signed report" notice.
    expect(container.querySelector('.report-print-notice')).toBeNull();
  });

  it('warns — on screen and in print — when the content changed after sign-out', async () => {
    mockSignedFamily(() =>
      checkResult({ matches: false, changed_sections: ['reported_variants', 'sequencing_qc'] }),
    );
    const { container } = renderPage();

    expect(
      await screen.findByText(/reported small variants and sequencing QC cut-offs/),
    ).toBeInTheDocument();
    expect(screen.getByText(/Changed since sign-out/)).toBeInTheDocument();
    // No check mark: the record line no longer claims the page is the signed report.
    expect(screen.queryByText(/✓ Signed out/)).not.toBeInTheDocument();
    expect(screen.getByText(/Signed out — version 2 by/)).toBeInTheDocument();
    expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
      /Not the signed report — the content differs from signed version 2/,
    );
  });

  it('names what the signed record could not capture (#514)', async () => {
    mockSignedFamily(() =>
      checkResult({
        matches: true,
        not_captured: [
          {
            section: 'sequencing_qc',
            item: 'Sequencing-QC cut-offs',
            reason: 'QC thresholds could not be resolved',
          },
          { section: 'modules', item: 'Monarch', reason: 'lookup failed' },
        ],
      }),
    );
    renderPage();

    const note = await screen.findByText(/Not captured in signed version 2:/);
    expect(note.closest('p')?.textContent).toMatch(
      /Sequencing-QC cut-offs \(QC thresholds could not be resolved\); Monarch \(lookup failed\)\./,
    );
  });

  it('shows no capture note for a complete signed record', async () => {
    mockSignedFamily(() => checkResult({ matches: true }));
    renderPage();

    await screen.findByText(/This page matches signed version 2/);
    expect(screen.queryByText(/Not captured in signed version/)).not.toBeInTheDocument();
  });

  it('treats the page as unsigned when the check cannot be made', async () => {
    mockSignedFamily(() => Promise.reject({ response: { status: 500, data: {} } }));
    const { container } = renderPage();

    expect(
      await screen.findByText(/could not be checked against signed version 2 — treat it as unsigned/),
    ).toBeInTheDocument();
    expect(screen.queryByText(/✓ Signed out/)).not.toBeInTheDocument();
    expect(container.querySelector('.report-print-notice')?.textContent).toMatch(/Not verified/);
  });

  it('marks an unsigned report as a draft in print', async () => {
    mockUnsignedFamily();
    const { container } = renderPage();

    await screen.findByRole('button', { name: /Sign out report/ });
    expect(container.querySelector('.report-print-notice')?.textContent).toMatch(
      /Draft — this report has not been signed/,
    );
  });

  it('downloads the frozen signed version', async () => {
    mockSignedFamily(() => checkResult({ matches: true }));
    const createObjectURL = vi.fn(() => 'blob:signed');
    const revokeObjectURL = vi.fn();
    Object.assign(URL, { createObjectURL, revokeObjectURL });
    // jsdom cannot navigate to a blob: URL; the click itself is what we need.
    const clickSpy = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    renderPage();

    fireEvent.click(await screen.findByRole('button', { name: /Download signed version 2/ }));
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    expect(apiMock.get).toHaveBeenCalledWith('/families/F1/report/sign-outs/2');
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:signed');
    expect(clickSpy).toHaveBeenCalledTimes(1);
    clickSpy.mockRestore();
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

  it('shows the frozen drift-override reason on the signed record', async () => {
    mockSignedFamily(() => checkResult({ matches: true }));
    const signed = { ...SIGNED_LATEST, drift_acknowledged: true, drift_acknowledgement_reason: 'reviewed' };
    const base = apiMock.get.getMockImplementation();
    apiMock.get.mockImplementation((url: string) =>
      url === '/families/F1/report/sign-outs'
        ? Promise.resolve({ data: { family_id: 'F1', latest: signed, signouts: [] } })
        : base!(url),
    );
    renderPage();

    expect(await screen.findByText(/override acknowledged:\s*reviewed/)).toBeInTheDocument();
    expect(screen.getByText('Evidence drift')).toBeInTheDocument();
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
      mockSignedFamily(() => checkResult({ matches: true }));
      const signed = {
        ...SIGNED_LATEST,
        import_incomplete_failed_datasets: ['snv', 'sv'],
        import_incomplete_job_id: IMPORT_JOB_ID,
        import_incomplete_acknowledged: true,
        import_incomplete_acknowledgement_reason: REASON,
      };
      const base = apiMock.get.getMockImplementation();
      apiMock.get.mockImplementation((url: string) =>
        url === '/families/F1/report/sign-outs'
          ? Promise.resolve({ data: { family_id: 'F1', latest: signed, signouts: [] } })
          : base!(url),
      );
      renderPage();

      const label = await screen.findByText('Incomplete import');
      expect(label.closest('p')).toHaveTextContent(
        `Incomplete import snv and sv not imported (import job ${IMPORT_JOB_ID}) — override acknowledged: ${REASON}`,
      );
    });

    it('names a change in import completeness since sign-out', async () => {
      mockSignedFamily(() =>
        checkResult({ matches: false, changed_sections: ['import_incomplete'] }),
      );
      renderPage();

      expect(await screen.findByText(/Changed since sign-out/)).toBeInTheDocument();
      expect(screen.getByText(/import completeness\. This page shows the current state/)).toBeInTheDocument();
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
