import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyReportPage from '../FamilyReportPage';
import { APP_VERSION_QUERY_KEY } from '../../../lib/appVersion';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { SIGNED_ANALYSIS, SIGNED_STATEMENT } from '../../../test/signedAnalysisFixture';

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

  // #725 follow-up — the report's SV list is a review-tag search over a capped candidate
  // read: past the cap, a tagged SV is missing from the page, which must say so.
  it('says, on screen and in print, when a list of reported variants came from a capped read', async () => {
    mockApi();
    const base = apiMock.get.getMockImplementation()!;
    apiMock.get.mockImplementation((url: string, config?: unknown) =>
      url.startsWith('/families/F1/structural-variants')
        ? Promise.resolve({
            data: { variants: [], total: 0, total_is_estimated: true, candidates_capped: true, candidate_limit: 50000 },
          })
        : base(url, config),
    );
    renderPage();

    const expected =
      /Incomplete — the list of reported structural variants may miss a variant: the search behind it stopped after the first 50,000 candidates of the callset, so a reported variant beyond that point is not shown\./;
    const alert = (await screen.findAllByRole('alert')).find((node) => expected.test(node.textContent ?? ''));
    expect(alert).toBeDefined();
    expect(alert).toHaveClass('no-print');
    // The printout carries it too, so a printed report cannot pass for the whole list.
    const printNotice = document.querySelector('.report-print-notice');
    expect(printNotice?.textContent).toMatch(expected);
  });

  // The report asked for 500 reported variants and never looked at the total: a longer list
  // ended at 500 without a trace, while the signed record holds every reported variant.
  it('asks for every reported variant, not the first 500', async () => {
    mockApi();
    renderPage();
    expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();

    const listUrls = apiMock.get.mock.calls
      .map(([url]) => String(url))
      .filter((url) => /^\/families\/F1\/(small|structural)-variants\?/.test(url));
    expect(listUrls.some((url) => url.startsWith('/families/F1/small-variants?'))).toBe(true);
    expect(listUrls.some((url) => url.startsWith('/families/F1/structural-variants?'))).toBe(true);
    listUrls.forEach((url) => {
      const params = new URLSearchParams(url.split('?')[1]);
      expect(params.get('review_tag')).toBe('report');
      expect(params.get('page_size')).toBe('10000');
    });
  });

  it('says, on screen and in print, when a list holds more reported variants than it shows', async () => {
    mockApi();
    const base = apiMock.get.getMockImplementation()!;
    apiMock.get.mockImplementation((url: string, config?: unknown) =>
      url.startsWith('/families/F1/small-variants')
        ? Promise.resolve({ data: { variants: [reportVariant], total: 3 } })
        : base(url, config),
    );
    renderPage();

    const expected = /Incomplete — the report lists 1 of the 3 reported small variants; the others are not shown\./;
    const alert = (await screen.findAllByRole('alert')).find((node) => expected.test(node.textContent ?? ''));
    expect(alert).toBeDefined();
    expect(alert).toHaveClass('no-print');
    expect(document.querySelector('.report-print-notice')?.textContent).toMatch(expected);
  });

  it('shows no capped-list notice when the reported lists are complete', async () => {
    mockApi();
    renderPage();
    expect(await screen.findByRole('heading', { name: /BRCA1 c\.123A>G/ })).toBeInTheDocument();
    expect(screen.queryByText(/may miss a variant/)).not.toBeInTheDocument();
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

  it('names the frozen frequencies and in-silico scores that moved since a classification (CLIN-7)', async () => {
    const item = (variantId: string, overrides: Record<string, unknown>) => ({
      variant_id: variantId,
      acmg_class: 'acmg_class_4',
      classified_by: 'alice',
      classified_at: null,
      status: 'drifted',
      annotation_version_from: 'v1',
      annotation_version_to: 'v1',
      clinvar_from: null,
      clinvar_to: null,
      changed: [],
      evidence_from: null,
      evidence_to: null,
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
            checked: 2,
            drifted_count: 2,
            drifted: [
              item('1-100-A-G', {
                changed: ['gnomad_af', 'revel', 'annotations'],
                evidence_from: { gnomad_af: 3.1e-5, revel: 0.412 },
                evidence_to: { gnomad_af: 0.012, revel: 0.951 },
              }),
              // Frozen before the values were, or only another annotation moved.
              item('2-200-C-T', { changed: ['annotations'] }),
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    expect(await screen.findByText(/2 classifications have evidence changes since being made/)).toBeInTheDocument();
    const items = screen.getAllByRole('listitem').map((listItem) => listItem.textContent ?? '');
    expect(items).toContain('1-100-A-G — gnomAD AF 3.10e-5 → 0.012; REVEL 0.412 → 0.951 (classified by alice)');
    expect(items).toContain('2-200-C-T — annotation set changed (classified by alice)');
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
            total: 1,
            has_more: false,
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
    const { container } = renderPage();

    expect(await screen.findByText('Classification audit trail')).toBeInTheDocument();
    expect(
      screen.getByText(/Classification unclassified → VUS \(class 3\)/),
    ).toBeInTheDocument();
    expect(screen.getByText('alice')).toBeInTheDocument();
    expect(screen.getByText(/2026-06-25 09:04 UTC/)).toBeInTheDocument();
    // The whole trail: nothing to say about older events, and nothing to load.
    expect(screen.queryByText(/most recent of/)).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Show older events' })).not.toBeInTheDocument();
    expect(container.querySelector('.report-print-notice')?.textContent).not.toMatch(/audit trail/);
  });

  // AUDIT-1 — the trail is served a page at a time. The report listed the 200 newest events
  // as the whole trail, which about 70 review saves fill.
  describe('a clinical audit trail longer than a page', () => {
    const auditEvent = (n: number) => ({
      id: `e${n}`,
      created_at: `2026-06-25T09:${String(n).padStart(2, '0')}:00Z`,
      variant_id: `1-${n}00-A-G`,
      actor: 'alice',
      action: 'classification',
      summary: `Classification change ${n}`,
      before: null,
      after: null,
    });
    // Three events, two to a page: the newest two, then the one before them.
    const FIRST_PAGE = { family_id: 'F1', total: 3, has_more: true, events: [auditEvent(3), auditEvent(2)] };
    const OLDER_PAGE = { family_id: 'F1', total: 3, has_more: false, events: [auditEvent(1)] };

    const mockAuditTrail = (olderPage: () => Promise<unknown>) => {
      apiMock.get.mockImplementation((url: string) => {
        if (url === '/families/F1') {
          return Promise.resolve({ data: { family_id: 'F1', members: [], projects: [] } });
        }
        if (url.startsWith('/families/F1/small-variants')) {
          return Promise.resolve({ data: { variants: [], total: 0 } });
        }
        if (url === '/families/F1/clinical-audit') return Promise.resolve({ data: FIRST_PAGE });
        if (url === '/families/F1/clinical-audit?before=e2') return olderPage();
        return Promise.resolve({ data: [] });
      });
    };
    const auditRequests = () =>
      apiMock.get.mock.calls.map(([url]) => String(url)).filter((url) => url.includes('/clinical-audit'));
    const auditItems = () =>
      Array.from(document.querySelectorAll('.report-audit-item .report-audit-summary')).map(
        (item) => item.textContent?.trim(),
      );

    it('says, on screen and in print, that it shows only the most recent events, and loads the older ones', async () => {
      mockAuditTrail(() => Promise.resolve({ data: OLDER_PAGE }));
      const { container } = renderPage();

      const shown = await screen.findByText(
        'Showing the 2 most recent of 3 events; the older ones are not shown.',
      );
      // Printed with the trail.
      expect(shown.closest('.no-print')).toBeNull();
      expect(auditItems()).toEqual(['1-300-A-G Classification change 3', '1-200-A-G Classification change 2']);
      // A printout of the report says so at the top of every page too.
      expect(container.querySelector('.report-print-notice')?.textContent).toContain(
        'Incomplete — the audit trail lists the 2 most recent of its 3 events; the older ones are not shown.',
      );
      expect(auditRequests()).toEqual(['/families/F1/clinical-audit']);

      // The next page holds the events recorded before the last one shown.
      const showOlder = screen.getByRole('button', { name: 'Show older events' });
      expect(showOlder.closest('.no-print')).not.toBeNull();
      fireEvent.click(showOlder);

      expect(await screen.findByText('Classification change 1')).toBeInTheDocument();
      expect(auditRequests()).toEqual(['/families/F1/clinical-audit', '/families/F1/clinical-audit?before=e2']);
      expect(auditItems()).toEqual([
        '1-300-A-G Classification change 3',
        '1-200-A-G Classification change 2',
        '1-100-A-G Classification change 1',
      ]);
      // The whole trail is shown: nothing more to say or to load.
      expect(screen.queryByText(/most recent of/)).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Show older events' })).not.toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).not.toMatch(/audit trail/);
    });

    it('keeps the events it shows when the older ones cannot be loaded, and still says it shows part of the trail', async () => {
      let olderFails = true;
      mockAuditTrail(() =>
        olderFails
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : Promise.resolve({ data: OLDER_PAGE }),
      );
      const { container } = renderPage();

      fireEvent.click(await screen.findByRole('button', { name: 'Show older events' }));

      expect(await screen.findByText('The older events could not be loaded.')).toBeInTheDocument();
      // Not the trail failing as a whole: what was read stays, and so does what it says.
      expect(screen.queryByText('The audit trail could not be loaded.')).not.toBeInTheDocument();
      expect(screen.queryByText(/Parts of this report could not be loaded/)).not.toBeInTheDocument();
      expect(auditItems()).toEqual(['1-300-A-G Classification change 3', '1-200-A-G Classification change 2']);
      expect(
        screen.getByText('Showing the 2 most recent of 3 events; the older ones are not shown.'),
      ).toBeInTheDocument();
      expect(container.querySelector('.report-print-notice')?.textContent).toContain(
        'Incomplete — the audit trail lists the 2 most recent of its 3 events',
      );

      // A second try reads them.
      olderFails = false;
      fireEvent.click(screen.getByRole('button', { name: 'Show older events' }));
      expect(await screen.findByText('Classification change 1')).toBeInTheDocument();
      expect(screen.queryByText('The older events could not be loaded.')).not.toBeInTheDocument();
      expect(screen.queryByText(/most recent of/)).not.toBeInTheDocument();
    });
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

  // Each concern names the samples its check read, as the backend words it (CLIN-8).
  const QC_FAILED_PAIR =
    'Samples CHILD and FATHER: recorded parent-child but genotypes look unrelated (kinship 0.010, IBS0 4.00%, 5000 sites) — possible sample swap or wrong parent.';
  const QC_409 = {
    response: {
      status: 409,
      data: {
        detail: {
          gate: 'sample_qc',
          message: "Sample-integrity QC status is 'fail' (possible swap)",
          qc_summary: {
            overall_status: 'fail',
            messages: [QC_FAILED_PAIR],
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
    // + the failing summary, each concern as the 409 gives it, naming its samples.
    const dialog = await screen.findByRole('dialog', { name: /acknowledgement required/i });
    expect(dialog).toBeInTheDocument();
    expect(screen.getByText(/possible swap/)).toBeInTheDocument();
    expect(
      within(dialog)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual([QC_FAILED_PAIR]);

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
    // (not 'fail'); the dialog must still fire and show the specific reason. Two samples
    // whose sex could not be checked read the same but for the sample each reason names
    // (CLIN-8); the dialog shows the message as the 409 gives it.
    const unverifiable = [
      'Sample PARTNER_A: no chrX genotypes available for sex inference.',
      'Sample PARTNER_B: no chrX genotypes available for sex inference.',
    ];
    const message = `A swap-relevant sample-integrity check could not be verified for an asserted pedigree relationship (${unverifiable.join('; ')}). A sample swap can present as missing data rather than a mismatch. Resolve it, or acknowledge with a reason to sign out anyway.`;
    const UNVERIFIABLE_QC_409 = {
      response: {
        status: 409,
        data: {
          detail: {
            gate: 'sample_qc',
            message,
            qc_summary: { overall_status: 'warn', messages: [] },
            unverifiable_checks: unverifiable,
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
    const dialog = await screen.findByRole('dialog', { name: /acknowledgement required/i });
    expect(within(dialog).getByText(message)).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText(/Reason for signing out/), {
      target: { value: 'Both partners were sexed by karyotype.' },
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
    signed_out_by: 'lab-signer',
    signed_out_at: '2026-06-25T10:00:00Z',
    content_hash: 'abc123def456',
    report_code: '7KQM2X-2-4PHT',
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
    report_code: '7KQM2X-1-B3HV',
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
    signed_out_by: 'lab-signer',
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
      expect(card).toHaveTextContent('Signed version 2 — signed out by lab-signer on 2026-06-25 10:00 UTC');
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
      expect(sv).toHaveTextContent('Pathogenic - class 5 · +1.00 pts');
      expect(sv).toHaveTextContent('2A+1.00 pts');
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

    // ClinGen CNV points as the classifier writes them (#735): signed and to two decimals, since
    // the class thresholds (0.99 / 0.90 / -0.90 / -0.99) differ in the second one.
    it('writes a CNV classification’s points to two decimals, as the classifier does', async () => {
      mockSignedCase({
        snapshot: {
          ...SIGNED_SNAPSHOT,
          reported_structural_variants: [
            {
              variant_id: '2-1000-50000-DEL---',
              variant_key: 123,
              classification: 'Uncertain significance - class 3',
              cnv_class: 'cnv_class_3',
              cnv_point_total: 0.6,
              cnv_acmg: {
                kind: 'loss',
                criteria: [
                  { code: '1A', points: 0, accepted: true, evidence: null, auto_suggested: false },
                  { code: '2C-1', points: 0.9, accepted: true, evidence: null, auto_suggested: false },
                  { code: '4L', points: -0.3, accepted: true, evidence: null, auto_suggested: false },
                ],
                point_total: 0.6,
                classification: 'Uncertain significance - class 3',
              },
              tags: ['report'],
              note: null,
            },
          ],
        },
      });
      renderPage();

      const sv = (await screen.findByRole('heading', { name: 'Structural variant 2-1000-50000-DEL---' })).closest(
        'article',
      )!;
      expect(sv).toHaveTextContent('Uncertain significance - class 3 · +0.60 pts');
      expect(sv).toHaveTextContent('1A0.00 pts');
      expect(sv).toHaveTextContent('2C-1+0.90 pts');
      expect(sv).toHaveTextContent('4L-0.30 pts');
      // A small variant's ACMG points are whole and keep their own format.
      const variant = screen.getByRole('heading', { name: '17-43000000-A-G' }).closest('article')!;
      expect(variant).toHaveTextContent('Likely Pathogenic - class 4 · 7 pts');
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

    // The signed analysis record (owner decisions of 2026-10-14): what the analysis looked at,
    // what the record is, and the report code the EPD report quotes, all from the record.
    it('shows what the analysis looked at, as the record froze it', async () => {
      mockSignedCase({ snapshot: { ...SIGNED_SNAPSHOT, ...SIGNED_ANALYSIS } });
      const { container } = renderPage();

      const card = await signedRecordCard();
      // What the record is, and the code the EPD report refers to it by: printed, not only shown.
      expect(card).toHaveTextContent(SIGNED_STATEMENT);
      const code = within(card).getByText('Report code').closest('p')!;
      expect(code).toHaveTextContent('Report code 7KQM2X-2-4PHT');
      expect(code.closest('.no-print')).toBeNull();

      // Each reported variant as the analyst reviewed it: gene, transcript, HGVS, consequence,
      // and each sample's genotype.
      const variant = screen.getByRole('heading', { name: '17-43000000-A-G' }).closest('article')!;
      const description = within(variant).getByRole('heading', { name: 'Variant description as reviewed' }).parentElement!;
      expect(description).toHaveTextContent('GENE_PATH c.10A>G p.(Lys4Arg), missense variant (MODERATE)');
      expect(description).toHaveTextContent('17:43,000,000 A → G · transcript ENST01 (MANE Select) · callset clair3');
      expect(description).toHaveTextContent('MOTHER 0/0 (depth 28, GQ 60)');
      expect(description).toHaveTextContent('PROBAND 0/1 (depth 30, AD 15,15, VAF 0.5)');
      const sv = screen.getByRole('heading', { name: 'Structural variant 2-1000-50000-DEL---' }).closest('article')!;
      expect(sv).toHaveTextContent('Deletion (copy loss), 2:1,000-50,000, 49,000 bp, genes GENE_DEL, 2p25.3, caller needlr');
      expect(sv).toHaveTextContent('PROBAND 0/1 (copy number 1, 18 supporting reads)');
      // The record now holds the description; it still lacks the rest of the live report.
      expect(screen.getAllByText('Not in the signed record: the segregation, gene and phenotype.')).toHaveLength(2);
      const absent = screen.getByRole('heading', { name: 'Not in the signed record' }).closest('section')!;
      expect(absent).toHaveTextContent('its description as the analyst reviewed it');
      expect(absent).toHaveTextContent('the population frequency and the in silico predictions the live report shows');
      expect(absent).not.toHaveTextContent('the variant description:');

      const section = (name: string) => screen.getByRole('heading', { name }).closest('section')!;
      expect(section('Gene panels applied')).toHaveTextContent(
        'Epilepsy, version 2, 120 genes; PanelApp panel 402, version 3.1 (confidence levels 3), imported 2026-09-01 08:00 UTC; first applied 2026-10-10 09:00 UTC by analyst',
      );
      expect(section('Repeat expansions (TRGT)')).toHaveTextContent(
        'HTT (HD_HTT), Huntington disease: Pathogenic (grey zone from 27, pathogenic from 36; motif CAG). PROBAND 0/1: 18, 40 repeats — Pathogenic',
      );
      expect(section('Repeat expansions (TRGT)')).toHaveTextContent(
        'against the repeat catalogue of 96 loci (CoGA built-in, STRchive), digest abcdef012345',
      );
      expect(section('Paraphase')).toHaveTextContent(
        'SMN1 (SMN1/SMN2) — status by smn1_cn: pathogenic = 0, carrier = 1, normal ≥ 2. PROBAND: Carrier (SMN1 CN 1), copy-number change',
      );
      expect(section('Paraphase')).toHaveTextContent('GBA (GBA/GBAP1) — no automatic call. No sample has a result.');
      const reference = section('Reference-data releases');
      expect(reference).toHaveTextContent(
        'Gene reference, for the reported variants’ genes: ClinGen dosage 2026-09-30 and 2026-08-30 · HGNC 2026-09-01',
      );
      expect(reference).toHaveTextContent('Not in the gene reference: NOT_LOADED.');
      expect(reference).toHaveTextContent(
        'Clinical CNV knowledgebase: clinical-cnv-kb, loaded 2026-10-01 09:00 UTC (release not stated)',
      );

      // Beside the modules in the footer, with the code.
      const footer = container.querySelector('footer')!;
      expect(footer).toHaveTextContent('Signed version 2 · signed out 2026-06-25 10:00 UTC · report code 7KQM2X-2-4PHT');
      expect(footer).toHaveTextContent(
        'Reference data as signed: gene reference ClinGen dosage 2026-09-30 and 2026-08-30 · HGNC 2026-09-01; clinical CNV knowledgebase clinical-cnv-kb, loaded 2026-10-01 09:00 UTC (release not stated); PanelApp Epilepsy 3.1',
      );

      // Still nothing of the family's current data.
      const requested = apiMock.get.mock.calls.map(([url]) => String(url));
      LIVE_ONLY_REQUESTS.forEach((live) =>
        expect(requested.filter((url) => url.startsWith(live))).toEqual([]),
      );
    });

    it('says which of these a record signed before them does not hold, and still shows it', async () => {
      mockSignedCase();
      const { container } = renderPage();

      const card = await signedRecordCard();
      // Its code is made from fields every record holds; the sentence says what it is.
      expect(within(card).getByText('Report code').closest('p')).toHaveTextContent('Report code 7KQM2X-2-4PHT');
      expect(card).toHaveTextContent(SIGNED_STATEMENT);
      const section = (name: string) => screen.getByRole('heading', { name }).closest('section')!;
      expect(section('Gene panels applied')).toHaveTextContent(
        'Not in the signed record: version 2 was signed before CoGA recorded the gene panels the analysis applied.',
      );
      expect(section('Repeat expansions (TRGT)')).toHaveTextContent(
        'Not in the signed record: version 2 was signed before CoGA recorded the repeat-expansion calls.',
      );
      expect(section('Paraphase')).toHaveTextContent(
        'Not in the signed record: version 2 was signed before CoGA recorded the Paraphase results.',
      );
      expect(section('Reference-data releases')).toHaveTextContent(
        'Not in the signed record: version 2 was signed before CoGA recorded the reference-data releases.',
      );
      expect(screen.queryByRole('heading', { name: 'Variant description as reviewed' })).not.toBeInTheDocument();
      expect(container.querySelector('footer')).toHaveTextContent('Reference data as signed: not in the signed record');
    });

    it('says what a record could not capture of what the analysis looked at', async () => {
      mockSignedCase({
        snapshot: {
          ...SIGNED_SNAPSHOT,
          ...SIGNED_ANALYSIS,
          paraphase: { unavailable: 'The Paraphase results could not be read.' },
          variant_descriptions: { unavailable: "The reported variants' descriptions could not be read." },
        },
      });
      renderPage();

      const paraphase = (await screen.findByRole('heading', { name: 'Paraphase' })).closest('section')!;
      expect(paraphase).toHaveTextContent(
        'Not captured: the Paraphase results could not be read when version 2 was signed (The Paraphase results could not be read.).',
      );
      const variant = screen.getByRole('heading', { name: '17-43000000-A-G' }).closest('article')!;
      expect(variant).toHaveTextContent(
        "The variant descriptions could not be read when version 2 was signed (The reported variants' descriptions could not be read.).",
      );
    });

    // A monogenic NIPT family's record holds its NIPT results (signed_record_nipt); no other
    // family's does.
    const NIPT_SAMPLE_QC = {
      ...SIGNED_SNAPSHOT.sample_qc,
      application: 'nipt',
      application_label: 'Monogenic NIPT (cfDNA)',
    };
    const SIGNED_NIPT = {
      samples: { father: 'FATHER1', maternal_plasma: 'CFDNA1', fetus: 'FETUS1' },
      fetal_fraction: {
        ff: 0.12,
        ff_computed: 0.12,
        ff_median: 0.118,
        ci_low: 0.109,
        ci_high: 0.131,
        n_sites: 40,
        method: 'category7_pooled',
        low_confidence: false,
        vaf_q05: 0.04,
        vaf_q95: 0.08,
        status: 'pass',
        message: '12.0% (95% CI 10.9%–13.1%) from 40 category-7 sites.',
      },
      de_novo_window: { strict_min: 0.04, strict_max: 0.08, loose_min: 0.03, loose_max: 0.1 },
      category_counts: { '1': 1, '3': 1, '7': 40, '8': 0 },
      filter_counts: { total_in: 48, failed_quality: 1, failed_artifact: 1, passed: 46, paternal_only: 1 },
      quality_failures: { low_quality: 1 },
      checks: {
        paternity: { status: 'pass', message: 'Paternity supported (36 of 40 (90%) of his homozygous alleles seen).' },
        fetal_sex: {
          call: 'discordant',
          status: 'fail',
          message:
            "The father's X alleles read female and the plasma's chrY coverage reads male: the two signals disagree; check the samples.",
          paternal_x: 'female',
          chry: 'male',
        },
        plasma_sample: { status: 'unknown', message: 'The panel has no chrY target: no sex profile.', profile: 'no_chrY_targets' },
      },
      target_coverage: {
        targets: 3,
        median_mean: 1500,
        q05_mean: 300,
        below_critical: 1,
        below_advisory: 1,
        zero_mean: 0,
        incomplete: 0,
        critical_mean_depth: 300,
        advisory_mean_depth: 1000,
        genes: [{ gene: 'GENEA', targets: 1, weak_targets: 1, min_mean: 250, mean_of_means: 250 }],
      },
      variants: [
        {
          variant_id: '1-1001-A-G',
          source: 'nipt',
          genes: ['GENE_DENOVO'],
          category: 1,
          category_label: 'de novo in fetus',
          maternal_state: 'hom_ref',
          fetal_inheritance: 'de_novo',
          expected_vaf: 0.06,
          observed_vaf: 0.06,
          confidence: 0.98,
          paternal_transmission_probability: null,
          maternal_allele_probability: null,
          fetal_hom_alt_probability: null,
          flags: ['father_no_call'],
          plasma: { alt_reads: 30, depth: 500, depth_estimated: false },
          father: { state: 'hom_ref', vaf: 0, depth: 50 },
          de_novo: { window: 'strict', score: 14, label: 'high', reasons: ['novel'], other_cfdna_carriers: 0 },
        },
      ],
      not_classified: [{ variant_id: '9-999-A-G', reason: 'not_in_data' }],
      recessive_genes: [
        {
          gene: 'GENE_RECESS',
          risk: 0.86,
          risk_uses_prior: false,
          maternal_variant_id: '1-3001-A-G',
          paternal_variant_id: '1-10000-A-G',
          maternal: [],
          paternal: [],
        },
      ],
      inputs: {
        target_tables: [{ sample: 'CFDNA1', file: 'cfdna.targets.tsv', targets: 3, loaded_at: '2026-10-01T09:00:00+00:00' }],
        artifact_list: { assay_key: 'nipt_cfdna', entries: 2, digest: 'abcdef01'.repeat(2), removed: 1, kept: [] },
      },
      model: { reference: 'R NIPT-M v0.5.1 validation (6 families with trio exomes)' },
    };

    it('shows a NIPT family’s NIPT results as the record froze them, in print too', async () => {
      mockSignedCase({ snapshot: { ...SIGNED_SNAPSHOT, ...SIGNED_ANALYSIS, sample_qc: NIPT_SAMPLE_QC, nipt: SIGNED_NIPT } });
      renderPage();

      const nipt = (await screen.findByRole('heading', { name: 'NIPT results' })).closest('section')!;
      expect(nipt.closest('.no-print')).toBeNull();
      expect(nipt).toHaveTextContent(
        'The monogenic NIPT analysis of the maternal plasma CFDNA1 against the father FATHER1, for fetus FETUS1',
      );
      expect(nipt).toHaveTextContent(
        'Fetal fraction: 12.0% (95% CI 10.9%–13.1%) from 40 category-7 sites; per-site median 11.8% — Pass; fetal window for de novo alleles 4.0%–8.0%',
      );
      expect(nipt).toHaveTextContent('Paternity: Pass — Paternity supported');
      expect(nipt).toHaveTextContent(
        "Fetal sex: Discordant (the father's X alleles read female; the plasma's chrY coverage reads male) — Fail — The father's X alleles read female",
      );
      expect(nipt).toHaveTextContent('Maternal plasma sample: Not run — The panel has no chrY target: no sex profile.');
      expect(nipt).toHaveTextContent(
        'Target coverage: median 1500x over 3 targets; 1 below 300x, 0 with uncovered bases; genes with a weak target: GENEA (1 of 1, lowest mean 250x)',
      );
      expect(nipt).toHaveTextContent(
        'Filters: 48 cfDNA calls: 1 quality-filtered, 1 artifact-filtered, 46 analysed; 1 paternal allele without a plasma call of its own; quality-filter failures: 1 quality (TLOD) below the minimum',
      );
      expect(nipt).toHaveTextContent('Categories: De novo in fetus: 1; Maternal het, inherited: 1; Paternal, transmitted: 40');
      // Each reported variant as the NIPT analysis classified it, with its de novo triage.
      expect(nipt).toHaveTextContent(
        '1-1001-A-G (GENE_DENOVO): Category 1 (de novo in fetus), confidence 0.98: maternal hom_ref, fetal de_novo; VAF 6.0% (expected 6.0%); plasma reads 30 of 500; father reference · 0.0% · 50×. de novo: high priority (14). Flags: father_no_call.',
      );
      expect(nipt).toHaveTextContent('9-999-A-G: not classified — it was not in the family’s NIPT data.');
      expect(nipt).toHaveTextContent('GENE_RECESS: 86.0% (maternal 1-3001-A-G, paternal 1-10000-A-G)');
      expect(nipt).toHaveTextContent('Per-target coverage: CFDNA1: cfdna.targets.tsv, 3 targets, loaded 2026-10-01 09:00 UTC');
      expect(nipt).toHaveTextContent('Artifact list: nipt_cfdna, 2 alleles, digest abcdef01abcd; 1 left out of this analysis');
      expect(nipt).toHaveTextContent('Model: R NIPT-M v0.5.1 validation (6 families with trio exomes).');
    });

    it('says a NIPT family’s record signed before CoGA froze the NIPT results does not hold them', async () => {
      mockSignedCase({ snapshot: { ...SIGNED_SNAPSHOT, sample_qc: NIPT_SAMPLE_QC } });
      renderPage();
      const nipt = (await screen.findByRole('heading', { name: 'NIPT results' })).closest('section')!;
      expect(nipt).toHaveTextContent(
        'Not in the signed record: version 2 was signed before CoGA recorded the NIPT results.',
      );
    });

    it('says when a NIPT family’s NIPT results could not be captured', async () => {
      mockSignedCase({
        snapshot: {
          ...SIGNED_SNAPSHOT,
          sample_qc: NIPT_SAMPLE_QC,
          nipt: { unavailable: 'The NIPT results could not be read.' },
        },
      });
      renderPage();
      const nipt = (await screen.findByRole('heading', { name: 'NIPT results' })).closest('section')!;
      expect(nipt).toHaveTextContent(
        'Not captured: the NIPT results could not be read when version 2 was signed (The NIPT results could not be read.).',
      );
    });

    it('says nothing of NIPT results in the record of a family that is no NIPT family', async () => {
      mockSignedCase({ snapshot: { ...SIGNED_SNAPSHOT, ...SIGNED_ANALYSIS } });
      renderPage();
      await screen.findByRole('heading', { name: 'Gene panels applied' });
      expect(screen.queryByRole('heading', { name: 'NIPT results' })).not.toBeInTheDocument();
      expect(screen.queryByText(/NIPT/)).not.toBeInTheDocument();
    });

    it('says when the record holds no description of a reported variant', async () => {
      mockSignedCase({
        snapshot: {
          ...SIGNED_SNAPSHOT,
          ...SIGNED_ANALYSIS,
          variant_descriptions: { small: [], structural: [] },
        },
      });
      renderPage();

      const variant = (await screen.findByRole('heading', { name: '17-43000000-A-G' })).closest('article')!;
      expect(variant).toHaveTextContent(
        'The record holds no description of this variant: it was not in the family’s data when version 2 was signed.',
      );
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

    it('shows the Sample QC verdict as signed, before and after a check not run counted as a warning', async () => {
      const noChrX = 'No chrX genotypes available for sex inference.';
      const note =
        'Sex could not be checked for sample PROBAND. A check that could not run counts as a warning, not a pass.';
      const signedQc = (overall: string, notes: string[]) => ({
        ...SIGNED_SNAPSHOT,
        sample_qc: {
          ...SIGNED_SNAPSHOT.sample_qc,
          overall_status: overall,
          sex_checks: [{ sample_id: 'PROBAND', status: 'skip', message: noChrX }],
          notes,
        },
      });
      const checksAtSignOut = async () =>
        (await screen.findByRole('heading', { name: 'Checks at sign-out' })).closest('section')!;

      // Signed before: the record froze a pass beside the check that did not run. The page
      // shows that verdict as the record holds it, never one re-derived from the checks.
      mockSignedCase({ snapshot: signedQc('pass', []) });
      const { unmount } = renderPage();
      const before = await checksAtSignOut();
      expect(before).toHaveTextContent('Overall: Pass (Trio WGS).');
      expect(before).toHaveTextContent(`Sex of PROBAND: Not run — ${noChrX}`);
      unmount();

      // Signed since: a warning, with the note naming the sample.
      mockSignedCase({ versions: [{ ...SIGNED_ENTRY, qc_status: 'warn' }], snapshot: signedQc('warn', [note]) });
      renderPage();
      const after = await checksAtSignOut();
      expect(after).toHaveTextContent('Overall: Warning (Trio WGS).');
      expect(after).toHaveTextContent(`Sex of PROBAND: Not run — ${noChrX}`);
      expect(within(after).getByText(note)).toBeInTheDocument();
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

    it('shows the frequencies and in-silico scores frozen with a classification, and those that had moved (CLIN-7)', async () => {
      const [reported] = SIGNED_SNAPSHOT.reported_variants;
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, drift_acknowledged: true, drift_acknowledgement_reason: 'REVEL re-reviewed' }],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          reported_variants: [
            {
              ...reported,
              evidence_snapshot: {
                ...reported.evidence_snapshot,
                transcript_id: 'ENST00000000001',
                frequencies: {
                  gnomad_af: 3.1e-5,
                  gnomad_exomes_af: null,
                  gnomad_genomes_af: 3.1e-5,
                  gnomad_popmax_af: null,
                  topmed_af: null,
                  gnomad_ac: 4,
                  gnomad_hom_count: 0,
                  gnomad_hemi_count: null,
                },
                in_silico: {
                  cadd_phred: 24.3,
                  revel: 0.412,
                  spliceai_max: 0.02,
                  sift: 'deleterious(0.01)',
                  polyphen: 'probably_damaging(0.981)',
                  alpha_missense_class: null,
                  alpha_missense_pathogenicity: null,
                },
              },
            },
          ],
          drift: {
            checked: 1,
            drifted_count: 1,
            drifted: [
              {
                variant_id: '17-43000000-A-G',
                status: 'drifted',
                clinvar_from: 'Likely_pathogenic',
                clinvar_to: 'Likely_pathogenic',
                changed: ['revel', 'annotations'],
                evidence_from: { revel: 0.412 },
                evidence_to: { revel: 0.951 },
                classified_by: 'alice',
              },
            ],
          },
          acknowledged_drift: true,
          drift_acknowledgement_reason: 'REVEL re-reviewed',
        },
      });
      renderPage();

      await signedRecordCard();
      const variant = screen.getByRole('heading', { name: '17-43000000-A-G' }).closest('article')!;
      expect(variant).toHaveTextContent('ClinVar reported Likely pathogenic, with annotation version v1');
      expect(variant).toHaveTextContent(
        'Read from its primary transcript ENST00000000001: population frequencies gnomAD AF 3.10e-5, ' +
          'gnomAD exomes AF not annotated, gnomAD genomes AF 3.10e-5, gnomAD popmax AF not annotated, ' +
          'TOPMed AF not annotated, gnomAD allele count 4, gnomAD homozygotes 0 and gnomAD hemizygotes not ' +
          'annotated; in silico scores CADD 24.3, REVEL 0.412, SpliceAI 0.02, SIFT deleterious(0.01), PolyPhen ' +
          'probably_damaging(0.981), AlphaMissense class not annotated and AlphaMissense score not annotated.',
      );
      const drift = screen.getByRole('heading', { name: 'Evidence drift at sign-out' }).closest('section')!;
      expect(drift).toHaveTextContent('17-43000000-A-G — REVEL 0.412 → 0.951 (classified by alice)');
      expect(drift).toHaveTextContent('Signed out over it, with the reason: REVEL re-reviewed.');
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
        'Superseded. Signed version 2, signed out by lab-signer on 2026-06-25 10:00 UTC, is the latest.',
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
      expect(await screen.findByText(/This page still matches signed version 2\./)).toHaveTextContent(
        'Signed version 2 (report code 7KQM2X-2-4PHT) was signed out by lab-signer on 2026-06-25 10:00 UTC.',
      );
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
    const V1_SIGNED = { ...SIGNED_ENTRY, version: 1, report_code: '7KQM2X-1-B3HV' };
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

    const card = await signedRecordCard(1);
    expect(card).toHaveTextContent('Signed version 1 — signed out by lab-signer on 2026-06-25 10:00 UTC');
    // The sign-out confirmed, with the report code the EPD report is to quote; on screen only.
    // (The refreshed sign-out list can draw the version a moment before the page has moved to
    // it, so the confirmation is waited for.)
    const confirmation = await within(card).findByRole('status');
    expect(confirmation).toHaveTextContent(
      'Signed out as version 1. Report code 7KQM2X-1-B3HV: the EPD report refers to this record by it.',
    );
    expect(confirmation).toHaveClass('no-print');
    expect(within(card).getByText('Report code').closest('p')).toHaveTextContent('Report code 7KQM2X-1-B3HV');
    expect(await screen.findByText('The family’s current data still matches this signed version.')).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /BRCA1/ })).not.toBeInTheDocument();
  });

  it('says on the live report what a sign-out is, and names the signed version’s report code', async () => {
    mockApi();
    renderPage();
    expect(
      await screen.findByText(
        'Signing out is the analytical sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient report happens in the EPD, which refers to the signed record by its report code.',
      ),
    ).toBeInTheDocument();
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

    // An import whose process stopped part-way left no flag: only its entry in
    // `import_unfinished`, which gates the same way.
    const STOPPED_JOB_ID = '7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
    const UNFINISHED = {
      [STOPPED_JOB_ID]: {
        job_id: STOPPED_JOB_ID,
        at: '2026-10-02T09:12:00+00:00',
        datasets: ['apcad', 'haplotypes', 'qdnaseq'],
        finished_datasets: ['apcad', 'qdnaseq'],
      },
    };

    it('lists an import that stopped part-way, and posts the acknowledgement', async () => {
      mockUnsignedFamily();
      const stopped409 = {
        response: {
          status: 409,
          data: {
            detail: {
              gate: 'import_incomplete',
              message:
                "This family's data is incomplete: an import (2026-10-02T09:12:00+00:00) stopped before it finished, so haplotypes may be partly written or missing (apcad, qdnaseq had finished).",
              import_incomplete: null,
              import_unfinished: {
                ...UNFINISHED,
                // Stopped after its last dataset had finished.
                'job-2': { job_id: 'job-2', at: null, datasets: ['snv'], finished_datasets: ['snv'] },
              },
            },
          },
        },
      };
      const posts: Array<Record<string, unknown>> = [];
      apiMock.post.mockImplementation((_url: string, body: Record<string, unknown>) => {
        posts.push(body);
        return posts.length === 1 ? Promise.reject(stopped409) : Promise.resolve({ data: { version: 1 } });
      });
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      const dialog = await screen.findByRole('dialog', {
        name: /Incomplete import acknowledgement required/i,
      });
      expect(within(dialog).getByText(/stopped before it finished/)).toBeInTheDocument();
      expect(
        within(dialog).getByText(
          `Not finished (import job ${STOPPED_JOB_ID}): haplotypes may be partly written or missing`,
        ),
      ).toBeInTheDocument();
      expect(
        within(dialog).getByText('Not finished (import job job-2): it had finished each of its datasets'),
      ).toBeInTheDocument();
      expect(within(dialog).queryByText(/Failed to import/)).not.toBeInTheDocument();

      fireEvent.change(within(dialog).getByLabelText(/despite the incomplete import/), {
        target: { value: REASON },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: /Sign out anyway/ }));

      await waitFor(() => expect(posts).toHaveLength(2));
      expect(posts[1]).toMatchObject({
        acknowledge_import_incomplete: true,
        import_incomplete_acknowledgement_reason: REASON,
      });
    });

    it('names each failed dataset’s job in the dialog and on the signed record', async () => {
      // A later failure kept an earlier one: sv's error is in another job's record.
      const flag = { ...IMPORT_FLAG, failed_jobs: { snv: IMPORT_JOB_ID, sv: 'job-earlier' } };
      mockUnsignedFamily();
      apiMock.post.mockImplementation(() =>
        Promise.reject({
          response: {
            status: 409,
            data: { detail: { gate: 'import_incomplete', message: 'incomplete', import_incomplete: flag } },
          },
        }),
      );
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      const dialog = await screen.findByRole('dialog', {
        name: /Incomplete import acknowledgement required/i,
      });
      expect(within(dialog).getByText(`Import job ${IMPORT_JOB_ID}: snv`)).toBeInTheDocument();
      expect(within(dialog).getByText('Import job job-earlier: sv')).toBeInTheDocument();
    });

    it('names each failed dataset’s job on the signed record', async () => {
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, import_incomplete_acknowledged: true, import_incomplete_acknowledgement_reason: REASON }],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          import_incomplete: { ...IMPORT_FLAG, failed_jobs: { snv: IMPORT_JOB_ID, sv: 'job-earlier' } },
          acknowledged_import_incomplete: true,
          import_incomplete_acknowledgement_reason: REASON,
        },
      });
      renderPage();

      await signedRecordCard();
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent(`Import jobs: snv in ${IMPORT_JOB_ID}; sv in job-earlier.`);
    });

    it('shows on the signed record an import that had not finished', async () => {
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, import_incomplete_acknowledged: true, import_incomplete_acknowledgement_reason: REASON }],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          import_unfinished: UNFINISHED,
          acknowledged_import_incomplete: true,
          import_incomplete_acknowledgement_reason: REASON,
        },
      });
      renderPage();

      await signedRecordCard();
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent(
        `An import (begun 2026-10-02 09:12 UTC), import job ${STOPPED_JOB_ID}, had begun writing the family’s data and had not finished: haplotypes may have been partly written or missing (apcad and qdnaseq had finished). Signed out over it, with the reason: ${REASON}`,
      );
      expect(checks).not.toHaveTextContent('imported completely');
    });

    // A single-file upload marks the family before its first ClickHouse write, and a deletion
    // leaves a mark when it could not put the family's SVs back (SAFE-28): entries of their
    // own in `import_unfinished`, with no job, named by their key.
    const UPLOAD_KEY = 'upload-0a1b2c3d4e5f60718293a4b5c6d7e8f9';
    const DELETION_KEY = 'deletion-9f8e7d6c5b4a39281706f5e4d3c2b1a0';
    const WRITE_MARKS = {
      [UPLOAD_KEY]: {
        job_id: null,
        at: '2026-10-14T08:30:00+00:00',
        datasets: ['sv_sniffles'],
        finished_datasets: [],
      },
      [DELETION_KEY]: {
        job_id: null,
        at: '2026-10-14T09:00:00+00:00',
        datasets: ['cnv', 'sv_needlr'],
        finished_datasets: [],
      },
    };

    it('lists an upload and a deletion that did not finish in the acknowledgement dialog', async () => {
      mockUnsignedFamily();
      apiMock.post.mockImplementation(() =>
        Promise.reject({
          response: {
            status: 409,
            data: {
              detail: {
                gate: 'import_incomplete',
                message:
                  "This family's data is incomplete: a single-file upload (2026-10-14T08:30:00+00:00) did not finish, so sv_sniffles may be partly written or missing.",
                import_incomplete: null,
                import_unfinished: WRITE_MARKS,
              },
            },
          },
        }),
      );
      renderPage();

      fireEvent.click(await screen.findByRole('button', { name: /Sign out report/ }));
      const dialog = await screen.findByRole('dialog', {
        name: /Incomplete import acknowledgement required/i,
      });
      expect(
        within(dialog).getByText('Not finished (single-file upload): sv_sniffles may be partly written or missing'),
      ).toBeInTheDocument();
      expect(
        within(dialog).getByText(
          'Not finished (deletion of a sample’s data): cnv, sv_needlr may be partly written or missing',
        ),
      ).toBeInTheDocument();
      expect(within(dialog).queryByText(/import job/)).not.toBeInTheDocument();
    });

    it('shows on the signed record an upload and a deletion that had not finished', async () => {
      mockSignedCase({
        versions: [{ ...SIGNED_ENTRY, import_incomplete_acknowledged: true, import_incomplete_acknowledgement_reason: REASON }],
        snapshot: {
          ...SIGNED_SNAPSHOT,
          import_unfinished: WRITE_MARKS,
          acknowledged_import_incomplete: true,
          import_incomplete_acknowledgement_reason: REASON,
        },
      });
      renderPage();

      await signedRecordCard();
      const checks = screen.getByRole('heading', { name: 'Checks at sign-out' }).closest('section')!;
      expect(checks).toHaveTextContent(
        'A single-file upload (begun 2026-10-14 08:30 UTC) had not finished: sv_sniffles may have been partly written or missing.',
      );
      expect(checks).toHaveTextContent(
        'A deletion of a sample’s data (begun 2026-10-14 09:00 UTC) had not finished: cnv and sv_needlr may have been partly written or missing.',
      );
      expect(checks).not.toHaveTextContent(/An import \(begun 2026-10-14/);
    });

    it('names a change in unfinished imports since sign-out', async () => {
      mockSignedCase({ check: () => checkResult({ matches: false, changed_sections: ['import_unfinished'] }) });
      renderPage(undefined, '/families/F1/report?view=live');

      expect(await screen.findByText(/Changed since sign-out/)).toBeInTheDocument();
      expect(screen.getByText(/unfinished imports\. This page shows the current state/)).toBeInTheDocument();
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
