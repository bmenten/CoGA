import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyNiptReportPage from '../FamilyNiptReportPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  // GET /version, the build the report footer names (TF-15 §1). Answered here for every
  // test, so each test's fake backend serves only the family.
  version: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: (...args: unknown[]) => (args[0] === '/version' ? apiMock.version() : apiMock.get(...args)),
  },
}));

const RUNNING_BUILD = { version: '0.2.0', git_sha: '0123456789abcdef' };

beforeEach(() => {
  apiMock.version.mockResolvedValue({ data: RUNNING_BUILD });
});

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    speciesName: 'Homo sapiens',
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    projectId: undefined,
    isLoading: false,
  }),
  formatResolvedReferenceLabel: ({ assemblyName }: { assemblyName?: string }) =>
    assemblyName || 'Not linked',
}));

const niptVariant = (
  id: string,
  gene: string,
  category: number,
  categoryLabel: string,
  { chr = '1', start = 100 }: { chr?: string; start?: number } = {},
) => ({
  _id: id,
  chr,
  start,
  end: start,
  type: 'SNV',
  ref: 'A',
  alt: 'G',
  gene,
  genotypes: [],
  nipt: {
    category,
    category_label: categoryLabel,
    maternal_state: 'het',
    fetal_inheritance: 'transmitted',
    expected_vaf: 0.05,
    observed_vaf: 0.05,
    confidence: 0.95,
    flags: [],
  },
});

const renderPage = (search = '?panel_id=panel-1') => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/families/NIPT001/nipt/report${search}`]}>
        <Routes>
          <Route path="/families/:familyId/nipt/report" element={<FamilyNiptReportPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('FamilyNiptReportPage', () => {
  it('renders fetal fraction, coverage QC and inheritance-grouped candidates', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [{ sample_id: 'CFDNA', role: 'proband', active: true }],
            metadata: { analysis_type: 'monogenic_nipt' },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/summary') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            fetal_fraction: {
              ff: 0.12,
              ci_low: 0.1,
              ci_high: 0.14,
              n_sites: 42,
              method: 'category7_pooled',
              low_confidence: false,
            },
            category_counts: {},
            filter_counts: {},
          },
        });
      }
      if (url === '/families/NIPT001/nipt/coverage') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            overall_median_on_target: 140,
            target_region_count: 3,
            per_region: [],
            min_depth: 20,
            min_covered_fraction: 0.9,
            low_coverage_regions: [
              { label: 'ARID1B', chr: '6', median_coverage: 8, covered_fraction: 1, reason: 'low_depth' },
            ],
          },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            total: 4,
            variants: [
              niptVariant('v1', 'ARID1B', 1, 'De novo in fetus'),
              niptVariant('v2', 'SCN1A', 7, 'Paternal, transmitted'),
              niptVariant('v3', 'CFTR', 4, 'Maternal het → hom fetus'),
              niptVariant('v4', 'NOISE1', 2, 'Maternal het, not inherited'),
            ],
          },
        });
      }
      if (url === '/panels/panel-1') {
        return Promise.resolve({ data: { _id: 'panel-1', name: 'Neurodevelopmental', version: 2 } });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage();

    // Header + fetal fraction with CI.
    expect(
      await screen.findByRole('heading', { name: /family NIPT001/i }),
    ).toBeInTheDocument();
    expect(screen.getByText(/12\.0%/)).toBeInTheDocument();
    expect(screen.getByText(/95% CI 10\.0%–14\.0%/)).toBeInTheDocument();

    // The scope names the panel it was run on, and says no other filter applies.
    expect(screen.getByText('Gene panel Neurodevelopmental (version 2).')).toBeInTheDocument();
    expect(screen.getByText(/No other filter of the NIPT page applies/)).toBeInTheDocument();
    // Every candidate in the scope is listed, so nothing says otherwise.
    expect(screen.getByText(/the 4 classified candidate variants in its scope/)).toBeInTheDocument();
    expect(screen.queryByText('This list is incomplete.')).not.toBeInTheDocument();
    expect(screen.queryByText(/The candidate list is incomplete/)).not.toBeInTheDocument();
    expect(screen.queryByText(/^Incomplete —/)).not.toBeInTheDocument();
    expect(screen.getByText('De novo in fetus (1)')).toBeInTheDocument();

    // Coverage QC flags the low-depth panel gene.
    expect(screen.getByText('Coverage QC')).toBeInTheDocument();
    expect(screen.getByText(/1 of 3 panel genes were not adequately interrogated/)).toBeInTheDocument();
    expect(screen.getByText(/8x median/)).toBeInTheDocument();

    // Candidates are grouped by inheritance.
    expect(screen.getByText(/De novo in fetus \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Dominant, transmitted \(1\)/)).toBeInTheDocument();
    expect(screen.getByText(/Recessive \/ biallelic risk \(1\)/)).toBeInTheDocument();
    // Category 2 falls into the "Other" catch-all.
    expect(screen.getByText(/Other categories \(1\)/)).toBeInTheDocument();
    expect(screen.getByText('SCN1A')).toBeInTheDocument();
    expect(screen.getByText('CFTR')).toBeInTheDocument();
  });

  it('states the quality checks and the coverage of the capture targets', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [{ sample_id: 'CFDNA', role: 'proband', active: true }],
            metadata: { analysis_type: 'monogenic_nipt' },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/summary') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            fetal_fraction: {
              ff: 0.2,
              ff_computed: 0.2,
              ff_median: 0.19,
              ci_low: 0.19,
              ci_high: 0.21,
              n_sites: 800,
              method: 'category7_pooled',
              low_confidence: false,
            },
            category_counts: {},
            filter_counts: {},
            qc: {
              de_novo_window: { strict_min: 0.07, strict_max: 0.14, loose_min: 0.05, loose_max: 0.18 },
              paternity: {
                hom_alt_transmitted: 99,
                hom_alt_not_transmitted: 1,
                het_transmitted: 500,
                het_not_transmitted: 500,
                hom_alt_rate: 0.99,
                het_rate: 0.5,
                status: 'pass',
                message: 'The father is consistent with paternity.',
              },
              fetal_sex: {
                call: 'female',
                paternal_x: 'female',
                x_transmitted: 30,
                x_not_transmitted: 0,
                informative_sites: 30,
                chry_profile: 'female_no_chrY_signal',
                y_ratio: 0,
                x_ratio: 1.25,
                chry_fetal_fraction: null,
              },
              plasma_profile_status: 'pass',
              plasma_profile_message: 'Female plasma; no chrY signal.',
              target_coverage: null,
              quality_failures: {},
              model: {
                reference: 'R NIPT-M v0.5.1 validation',
                overdispersion: 0.0037,
                maternal_het_bias: -0.011,
                min_quality: 20,
                min_alt_reads: 5,
                min_vaf: 0.01,
                vaf_ff_fraction: 0.25,
                max_strand_bias_fs: 20,
                father_het_min_vaf: 0.2,
                father_hom_alt_min_vaf: 0.8,
                min_father_depth: 20,
              },
            },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/coverage') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            overall_median_on_target: null,
            target_region_count: 0,
            per_region: [],
            min_depth: 20,
            min_covered_fraction: 0.9,
            low_coverage_regions: [],
            targets: {
              targets: 12,
              median_mean: 950,
              q05_mean: 400,
              below_critical: 1,
              below_advisory: 5,
              zero_mean: 0,
              incomplete: 2,
              critical_mean_depth: 300,
              advisory_mean_depth: 1000,
              genes: [
                { gene: 'GENEA', targets: 4, weak_targets: 2, min_mean: 250, mean_of_means: 700, weak: [] },
                { gene: 'GENEB', targets: 0, weak_targets: 0, min_mean: null, mean_of_means: null, weak: [] },
                { gene: 'GENEC', targets: 8, weak_targets: 0, min_mean: 900, mean_of_means: 1100, weak: [] },
              ],
            },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [] } });
      }
      if (url === '/panels/panel-1') {
        return Promise.resolve({ data: { _id: 'panel-1', name: 'Neurodevelopmental', version: 2 } });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage();

    expect(await screen.findByText('Quality checks')).toBeInTheDocument();
    expect(screen.getByText(/the per-site median gives 19\.0%/)).toBeInTheDocument();
    expect(screen.getByText('Fetal sex: female.')).toBeInTheDocument();
    expect(screen.getByText(/30 seen, 0 absent \(female\); chrY coverage: female no chrY signal/)).toBeInTheDocument();
    expect(screen.getByText('Paternity (pass).')).toBeInTheDocument();
    expect(screen.getByText('The father is consistent with paternity.')).toBeInTheDocument();
    expect(screen.getByText('Maternal plasma sample (pass).')).toBeInTheDocument();
    expect(screen.getByText('Model: R NIPT-M v0.5.1 validation.')).toBeInTheDocument();
    // The coverage of the capture targets in scope, and the genes with a weak target.
    expect(screen.getByText(/The median depth of the 12 capture targets in scope is/)).toBeInTheDocument();
    expect(screen.getByText(/2 of 4 targets weak \(lowest mean 250x\)/)).toBeInTheDocument();
    expect(screen.getByText(/not a capture target/)).toBeInTheDocument();
    expect(screen.queryByText('GENEC')).not.toBeInTheDocument();
  });

  it('shows a not-configured message for a non-NIPT family', async () => {
    apiMock.get.mockResolvedValue({
      data: { family_id: 'FAM001', members: [], metadata: {} },
    });

    renderPage();

    expect(
      await screen.findByRole('heading', { name: /not a monogenic nipt family/i }),
    ).toBeInTheDocument();
  });

  // #605 — a failed request is never printed as a report without that part.
  describe('when a request fails', () => {
    const failing = (...urls: string[]) =>
      apiMock.get.mockImplementation((url: string) => {
        if (urls.includes(url)) {
          return Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
        }
        if (url === '/families/NIPT001') {
          return Promise.resolve({
            data: { family_id: 'NIPT001', members: [], metadata: { analysis_type: 'monogenic_nipt' } },
          });
        }
        if (url === '/families/NIPT001/nipt/variants') {
          return Promise.resolve({
            data: { family_id: 'NIPT001', total: 1, variants: [niptVariant('v1', 'ARID1B', 1, 'De novo in fetus')] },
          });
        }
        return Promise.resolve({ data: {} });
      });

    it('does not render an empty report when the family could not be loaded', async () => {
      failing('/families/NIPT001');
      renderPage();

      expect(await screen.findByText('Report could not be loaded')).toBeInTheDocument();
      expect(screen.getByText(/This is not a report without candidates/)).toBeInTheDocument();
      expect(screen.queryByText(/No classified candidate variants were returned/)).not.toBeInTheDocument();
    });

    it('says the fetal fraction and the coverage QC could not be loaded, on screen and in print', async () => {
      failing('/families/NIPT001/nipt/summary', '/families/NIPT001/nipt/coverage');
      renderPage();

      expect(await screen.findByText(/The fetal-fraction estimate could not be loaded/)).toBeInTheDocument();
      expect(screen.getByText(/The coverage QC could not be loaded/)).toBeInTheDocument();
      expect(
        screen.getByText(/^Incomplete — the fetal-fraction estimate and the coverage QC could not be loaded/),
      ).toBeInTheDocument();
      // Neither reads as a finding: no estimate, or no target regions.
      expect(screen.queryByText(/No fetal-fraction estimate is available/)).not.toBeInTheDocument();
      expect(screen.queryByText(/No target regions were available/)).not.toBeInTheDocument();
      // The candidates that did load are still reported.
      expect(screen.getByText(/De novo in fetus \(1\)/)).toBeInTheDocument();
    });

    it('says the software version could not be loaded, on screen and in print, and retries it', async () => {
      failing();
      let versionFails = true;
      apiMock.version.mockImplementation(() =>
        versionFails
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : Promise.resolve({ data: RUNNING_BUILD }),
      );
      renderPage();

      const footer = (await screen.findByText(/Report generated .* UTC/)).closest('footer') as HTMLElement;
      expect(
        await within(footer).findByText('CoGA — the version could not be loaded'),
      ).toBeInTheDocument();
      expect(
        screen.getByText(/^Incomplete — the software version could not be loaded/),
      ).toBeInTheDocument();

      versionFails = false;
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

  // TF-15 §1: the version is "in every report footer" — the NIPT report had no footer.
  it('names the generation time, the running build and the device label in the report footer', async () => {
    apiMock.get.mockImplementation((url: string) =>
      url === '/families/NIPT001'
        ? Promise.resolve({
            data: { family_id: 'NIPT001', members: [], metadata: { analysis_type: 'monogenic_nipt' } },
          })
        : Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [] } }),
    );
    renderPage();

    const footer = (await screen.findByText(/Report generated .* UTC/)).closest('footer') as HTMLElement;
    expect(await within(footer).findByText('CoGA 0.2.0 (0123456)')).toBeInTheDocument();
    expect(footer).toHaveTextContent('Software: CoGA 0.2.0 (0123456)');
    expect(footer).toHaveTextContent(
      'In-house IVD per IVDR Article 5(5) · Not CE-marked · For internal CMGG use only',
    );
    expect(footer).toHaveTextContent(
      'Manufacturer: Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent',
    );
    expect(screen.queryByText(/so this printout does not show the whole report/)).not.toBeInTheDocument();
  });

  // A NIPT family whose candidate list answers with `variants`, and whose gene panel 1 is
  // `panel`; the URLs in `failing` answer with a server error.
  const serveReport = ({
    variants = {},
    panel = { _id: 'panel-1', name: 'Cardiomyopathy', version: 3 },
    failing = [],
  }: {
    variants?: Record<string, unknown>;
    panel?: Record<string, unknown>;
    failing?: string[];
  }) =>
    apiMock.get.mockImplementation((url: string) => {
      if (failing.includes(url)) {
        return Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
      }
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: { family_id: 'NIPT001', members: [], metadata: { analysis_type: 'monogenic_nipt' } },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [], ...variants } });
      }
      if (url === '/panels/panel-1') {
        return Promise.resolve({ data: panel });
      }
      return Promise.resolve({ data: {} });
    });

  // The report asks for one page of candidates, and the list behind it classifies only the
  // first variants of the scope: it printed the part it had as the whole.
  describe('when it lists fewer candidates than its scope holds', () => {
    const inGenomicOrder = [
      niptVariant('v1', 'SCN1A', 7, 'Paternal, transmitted', { chr: '2', start: 166_000_000 }),
      niptVariant('v2', 'ARID1B', 1, 'De novo in fetus', { chr: '6', start: 157_100_000 }),
      niptVariant('v3', 'CFTR', 2, 'Maternal het, not inherited', { chr: '7', start: 117_559_590 }),
    ];
    const statementParagraph = async () =>
      (await screen.findByText('This list is incomplete.')).closest('p') as HTMLElement;

    it('says how many it lists of how many, where the list stops and why, on screen and in print', async () => {
      serveReport({ variants: { total: 1234, variants: inGenomicOrder } });
      renderPage();

      expect(await statementParagraph()).toHaveTextContent(
        'This list is incomplete. It shows 3 of the 1,234 candidate variants in this scope: a report ' +
          'lists at most 500, in genomic order. The list stops at chr7:117,559,590. Narrow the scope ' +
          'with a gene panel or a gene on the NIPT page, then open the report again.',
      );
      expect(
        screen.getByText(
          'Incomplete — this report lists 3 of the 1,234 candidate variants in its scope, so this ' +
            'printout does not show every candidate.',
        ),
      ).toHaveClass('print-only');
      expect(screen.getByText(/The candidate list is incomplete/).closest('section')).toHaveAttribute(
        'role',
        'alert',
      );
      expect(screen.getByText(/3 of the 1,234 classified candidate variants in its scope/)).toBeInTheDocument();
      // A group counts what it lists; an empty group does not say the scope has none.
      expect(screen.getByText('De novo in fetus (1 listed)')).toBeInTheDocument();
      expect(screen.getByText('Recessive / biallelic risk (0 listed)')).toBeInTheDocument();
      expect(screen.getByText('Other categories (1 listed)')).toBeInTheDocument();
      expect(screen.getByText('None among the listed variants.')).toBeInTheDocument();
      expect(screen.queryByText('No candidates in this group.')).not.toBeInTheDocument();
    });

    it.each([
      [
        'more of them than one report lists',
        { total: 812, total_is_estimated: true, count_limit: 5000 },
        'More than 5,000 variants matched this scope, and CoGA classifies at most 5,000 at once, in ' +
          'genomic order, so this scope holds at least 812 candidate variants. It shows 3 of them: a ' +
          'report lists at most 500.',
        'Incomplete — this report lists 3 of at least 812 candidate variants in its scope, so this ' +
          'printout does not show every candidate.',
      ],
      [
        'every one it classified listed',
        { total: 3, total_is_estimated: true, count_limit: 5000 },
        'More than 5,000 variants matched this scope, and CoGA classifies at most 5,000 at once, in ' +
          'genomic order.',
        'Incomplete — more variants matched the scope of this report than CoGA classifies at once, so ' +
          'this printout does not show every candidate.',
      ],
    ])('says when more variants matched than CoGA classifies, with %s', async (_case, page, why, printed) => {
      serveReport({ variants: { ...page, variants: inGenomicOrder } });
      renderPage();

      const statement = await statementParagraph();
      expect(statement).toHaveTextContent(why);
      expect(statement).toHaveTextContent('The list stops at chr7:117,559,590.');
      expect(screen.getByText(printed)).toHaveClass('print-only');
    });
  });

  // The report applies only the gene panel and the gene of the NIPT page's search; it did
  // not say so, nor name them.
  describe('its scope', () => {
    it.each([
      ['?panel_id=panel-1', 'Gene panel Cardiomyopathy (version 3).'],
      ['?gene=MYH7,TNNT2', 'Genes MYH7 and TNNT2.'],
      [
        '?panel_id=panel-1&gene=MYH7',
        'Gene panel Cardiomyopathy (version 3) and gene MYH7: a variant is listed when it matches both.',
      ],
      ['', 'No gene panel or gene was chosen: the report covers every variant of the family.'],
    ])('names the gene panel and the genes it applies (%s)', async (search, scope) => {
      serveReport({});
      renderPage(search);

      expect(await screen.findByText(scope)).toBeInTheDocument();
      expect(screen.getByText(/No other filter of the NIPT page applies/)).toHaveTextContent(
        'except the sites on the recurrent-artifact list of the assay',
      );
      if (!search.includes('panel_id')) {
        expect(apiMock.get.mock.calls.some(([url]) => String(url).startsWith('/panels'))).toBe(false);
      }
    });

    it('asks for the candidates with the gene panel and the gene, and no other filter', async () => {
      serveReport({});
      renderPage('?panel_id=panel-1&gene=MYH7&project_id=P1&category=1&max_gnomad_af=0.01');

      await screen.findByText(/No other filter of the NIPT page applies/);
      const variantRequests = apiMock.get.mock.calls.filter(
        ([url]) => url === '/families/NIPT001/nipt/variants',
      );
      expect(variantRequests.map(([, config]) => config.params)).toEqual([
        { page: 1, page_size: 500, panel_id: 'panel-1', gene: 'MYH7' },
      ]);
    });

    it('says the name of the gene panel could not be loaded, on screen and in print, and retries it', async () => {
      serveReport({ failing: ['/panels/panel-1'] });
      renderPage();

      expect(await screen.findByText('Gene panel panel-1 (its name could not be loaded).')).toBeInTheDocument();
      expect(
        screen.getByText(/^Incomplete — the name of the gene panel could not be loaded/),
      ).toHaveClass('print-only');

      serveReport({});
      fireEvent.click(
        within(screen.getByText(/Parts of this report could not be loaded/).closest('section')!).getByRole(
          'button',
          { name: 'Retry' },
        ),
      );

      expect(await screen.findByText('Gene panel Cardiomyopathy (version 3).')).toBeInTheDocument();
      expect(screen.queryByText(/its name could not be loaded/)).not.toBeInTheDocument();
    });
  });
});
