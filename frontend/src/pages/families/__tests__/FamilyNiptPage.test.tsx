import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyNiptPage from '../FamilyNiptPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../../../test/familyPageBanners';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

const FETAL_FRACTION = {
  ff: 0.1,
  ff_computed: 0.1,
  ff_median: 0.1,
  ci_low: 0.095,
  ci_high: 0.105,
  n_sites: 40,
  method: 'category7_pooled',
  low_confidence: false,
};

const renderPage = (familyId: string) => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[`/families/${familyId}/nipt`]}>
        <Routes>
          <Route path="/families/:familyId/nipt" element={<FamilyNiptPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('FamilyNiptPage', () => {
  it('renders the fetal fraction, funnel, categories, and classified variants', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [],
            metadata: { analysis_type: 'monogenic_nipt' },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/summary') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            fetal_fraction: FETAL_FRACTION,
            category_counts: { '1': 1, '3': 2, '7': 40 },
            filter_counts: { total_in: 100, failed_quality: 5, failed_artifact: 3, passed: 92 },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/coverage') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            overall_median_on_target: 120,
            target_region_count: 2,
            per_region: [
              {
                label: 'BRCA1',
                chr: '17',
                start: 100,
                end: 200,
                median_coverage: 118,
                covered_bases: 100,
                target_bases: 100,
              },
              {
                label: 'ARID1B',
                chr: '6',
                start: 100,
                end: 200,
                median_coverage: 8,
                covered_bases: 100,
                target_bases: 100,
              },
            ],
            min_depth: 20,
            min_covered_fraction: 0.9,
            low_coverage_regions: [
              {
                label: 'ARID1B',
                chr: '6',
                median_coverage: 8,
                covered_fraction: 1.0,
                reason: 'low_depth',
              },
            ],
          },
        });
      }
      if (url === '/panels') {
        return Promise.resolve({
          data: [{ _id: 'panel-1', name: 'Intellectual disability' }],
        });
      }
      if (url === '/families/NIPT001/small-variant-tags') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/NIPT001/small-variant-filter-presets') {
        return Promise.resolve({ data: [] });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        // The endpoint now returns the full small-variant payload plus a `nipt`
        // classification block, so the page renders it via SmallVariantResults.
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            total: 1,
            fetal_fraction: FETAL_FRACTION,
            variants: [
              {
                _id: '1-100-A-G',
                chr: '1',
                start: 100,
                end: 100,
                type: 'SNV',
                ref: 'A',
                alt: 'G',
                gene: 'BRCA1',
                impact: 'HIGH',
                effect: 'missense_variant',
                genotypes: [],
                nipt: {
                  category: 7,
                  category_label: 'paternal, transmitted to fetus',
                  maternal_state: 'hom_ref',
                  fetal_inheritance: 'paternal_transmitted',
                  expected_vaf: 0.05,
                  observed_vaf: 0.05,
                  confidence: 0.97,
                  flags: [],
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage('NIPT001');

    // SV-style header: "Family <id>" with the Monogenic NIPT kicker.
    expect(
      await screen.findByRole('heading', { name: /family NIPT001/i }),
    ).toBeInTheDocument();

    // Fetal fraction + filter funnel are folded into the header summary badges.
    expect(await screen.findByText('Fetal fraction 10.0%')).toBeInTheDocument();
    expect(screen.getByText('Analysed 92')).toBeInTheDocument();

    // The reused small-variant filter form's gene-panel select and the NIPT
    // Categories section (replacing the genotype/inheritance subsection) are
    // present, with per-category counts sourced from the summary.
    expect(screen.getByLabelText('Quick gene panel')).toBeInTheDocument();
    expect(screen.getByText(/7 — Paternal, transmitted/)).toBeInTheDocument();
    // NIPT built-in presets + custom-save chrome are present (like the SV page).
    expect(screen.getByRole('option', { name: 'De novo' })).toBeInTheDocument();
    expect(
      screen.getByRole('option', { name: 'Recessive (both parents carrier)' }),
    ).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Save current' })).toBeInTheDocument();

    // On-target coverage comes from the coverage query.
    expect(await screen.findByText('120x')).toBeInTheDocument();
    expect(screen.getByText('On-target coverage')).toBeInTheDocument();

    // The coverage QC counts the panel genes below the depth threshold; the coverage page
    // the link opens names them.
    expect(screen.getByText(/1 of 2 panel genes below QC/)).toHaveAttribute('role', 'status');
    expect(screen.queryByText(/ARID1B/)).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Coverage details' })).toHaveAttribute(
      'href',
      '/families/NIPT001/nipt/coverage',
    );

    // The variant renders through the reused small-variant card (≤100 results →
    // cards view), with the NIPT classification block layered on. (BRCA1 appears
    // in the gene link and external-resource links, hence findAllByText.)
    expect((await screen.findAllByText('BRCA1')).length).toBeGreaterThan(0);
    expect(screen.getByText(/paternal, transmitted to fetus/)).toBeInTheDocument();
    expect(screen.getByText('Category 7')).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 1')).toBeInTheDocument();
    // Every variant of the search was classified, so nothing says the list stops short.
    expect(screen.queryByText(/than CoGA classifies at once/)).not.toBeInTheDocument();

    // A category tick survives "Apply filters" (it round-trips through the URL rather
    // than being cleared by the URL-sync effect).
    const cat7 = screen.getByRole('checkbox', { name: /7 — Paternal, transmitted/ });
    expect(cat7).not.toBeChecked();
    fireEvent.click(cat7);
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /7 — Paternal, transmitted/ })).toBeChecked(),
    );

    // Picking an inheritance view clears the ticks (the view reads the fetal inheritance
    // itself) and offers the view's own control.
    fireEvent.change(screen.getByLabelText('NIPT inheritance preset'), {
      target: { value: 'paternal_dominant' },
    });
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /7 — Paternal, transmitted/ })).not.toBeChecked(),
    );
    expect(screen.getByRole('checkbox', { name: /Also list the alleles the fetus did not inherit/ })).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('NIPT inheritance preset'), { target: { value: 'de_novo' } });
    expect(await screen.findByLabelText('Lowest de novo priority')).toBeInTheDocument();
  });

  // A fresh open (no search in the URL) lists the de novo candidates: the De novo preset.
  it('opens on the De novo preset', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [
              { sample_id: 'CFDNA', role: 'proband', active: true },
              { sample_id: 'FATHER', role: 'father', active: true },
            ],
            metadata: { analysis_type: 'monogenic_nipt' },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({
          data: { family_id: 'NIPT001', total: 0, fetal_fraction: FETAL_FRACTION, variants: [] },
        });
      }
      if (url === '/panels' || url.endsWith('/small-variant-tags') || url.endsWith('-presets')) {
        return Promise.resolve({ data: [] });
      }
      return Promise.resolve({ data: {} });
    });

    renderPage('NIPT001');

    await waitFor(() =>
      expect(
        apiMock.get.mock.calls.some(
          ([url, config]) =>
            url === '/families/NIPT001/nipt/variants' && config?.params?.inheritance === 'de_novo',
        ),
      ).toBe(true),
    );
    const variantCalls = apiMock.get.mock.calls.filter(
      ([url]) => url === '/families/NIPT001/nipt/variants',
    );
    // No unfiltered search goes out before the preset applies.
    expect(variantCalls.every(([, config]) => config?.params?.inheritance === 'de_novo')).toBe(true);
    const { params } = variantCalls[0][1];
    expect(params.impact).toEqual(['HIGH', 'MODERATE']);
    expect(params.clinvar_overrides_frequency).toBe(true);
    expect(screen.getByLabelText('NIPT inheritance preset')).toHaveValue('de_novo');
  });

  // The list classifies the first variants of the search, in genomic order, up to a limit.
  // Past it, the list stopped part-way through the genome and read as complete.
  it('says when the list stops at the classification limit', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: { family_id: 'NIPT001', members: [], metadata: { analysis_type: 'monogenic_nipt' } },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            total: 1,
            total_is_estimated: true,
            count_limit: 5000,
            fetal_fraction: FETAL_FRACTION,
            variants: [
              {
                _id: '1-100-A-G',
                chr: '1',
                start: 100,
                end: 100,
                type: 'SNV',
                ref: 'A',
                alt: 'G',
                gene: 'BRCA1',
                genotypes: [],
                nipt: {
                  category: 7,
                  category_label: 'paternal, transmitted to fetus',
                  maternal_state: 'hom_ref',
                  fetal_inheritance: 'paternal_transmitted',
                  expected_vaf: 0.05,
                  observed_vaf: 0.05,
                  confidence: 0.97,
                  flags: [],
                },
              },
            ],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });

    renderPage('NIPT001');

    expect(
      await screen.findByText(
        'More variants matched this search than CoGA classifies at once (5,000), so the list ' +
          'stops part-way through the genome and its count is a lower bound. Narrow the search ' +
          'with a gene panel, a gene or a region.',
      ),
    ).toHaveAttribute('role', 'status');
  });

  it('shows a loader until the analysis has loaded', async () => {
    let resolveSummary: (value: unknown) => void = () => undefined;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: { family_id: 'NIPT001', members: [], metadata: { analysis_type: 'monogenic_nipt' } },
        });
      }
      if (url === '/families/NIPT001/nipt/summary') {
        return new Promise((resolve) => {
          resolveSummary = resolve;
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [] } });
      }
      return Promise.resolve({ data: [] });
    });

    renderPage('NIPT001');

    // Not an empty page while the fetal fraction is estimated.
    expect(await screen.findByRole('heading', { name: 'Loading the NIPT analysis' })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: /family NIPT001/i })).not.toBeInTheDocument();

    resolveSummary({
      data: {
        family_id: 'NIPT001',
        fetal_fraction: FETAL_FRACTION,
        category_counts: { '1': 1 },
        filter_counts: { total_in: 100, failed_quality: 5, failed_artifact: 3, passed: 92 },
      },
    });
    expect(await screen.findByRole('heading', { name: /family NIPT001/i })).toBeInTheDocument();
    expect(screen.queryByRole('heading', { name: 'Loading the NIPT analysis' })).not.toBeInTheDocument();
  });

  // A NIPT family can be partly imported like any other, and on an assembly outside the
  // validated scope: the header warns of both, as on every family page.
  it('warns in its header that the import is incomplete and the assembly is not validated', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/NIPT001') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            members: [],
            projects: ['p1'],
            metadata: { analysis_type: 'monogenic_nipt', ...INCOMPLETE_IMPORT_METADATA },
          },
        });
      }
      if (url === '/projects') return Promise.resolve({ data: [offScopeProject('p1')] });
      if (url === '/families/NIPT001/nipt/summary') {
        return Promise.resolve({
          data: {
            family_id: 'NIPT001',
            fetal_fraction: FETAL_FRACTION,
            category_counts: {},
            filter_counts: { total_in: 0, failed_quality: 0, failed_artifact: 0, passed: 0 },
          },
        });
      }
      if (url === '/families/NIPT001/nipt/variants') {
        return Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [] } });
      }
      return Promise.resolve({ data: [] });
    });

    renderPage('NIPT001');

    const header = (await screen.findByRole('heading', { name: /family NIPT001/i })).closest('.page-top-card');
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
  });

  it('shows a not-configured message for a non-NIPT family', async () => {
    apiMock.get.mockResolvedValue({
      data: { family_id: 'FAM001', members: [], metadata: {} },
    });

    renderPage('FAM001');

    await waitFor(() => {
      expect(
        screen.getByRole('heading', { name: /not a monogenic nipt family/i }),
      ).toBeInTheDocument();
    });
  });

  // #606 — a failed request is said as such: never as no variants, no fetal fraction, or
  // zero counts.
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
        if (url === '/families/NIPT001/nipt/summary') {
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              fetal_fraction: FETAL_FRACTION,
              category_counts: { '1': 1 },
              filter_counts: { total_in: 100, failed_quality: 5, failed_artifact: 3, passed: 92 },
            },
          });
        }
        if (url === '/families/NIPT001/nipt/variants') {
          return Promise.resolve({ data: { family_id: 'NIPT001', total: 0, variants: [] } });
        }
        return Promise.resolve({ data: [] });
      });

    it('says a failed variant search failed, not that no variants match', async () => {
      failing('/families/NIPT001/nipt/variants');
      renderPage('NIPT001');

      expect(await screen.findByText(/Could not load the NIPT variants — this is not an empty result/)).toBeInTheDocument();
      expect(screen.queryByText(/No variants match the current search/)).not.toBeInTheDocument();
    });

    it('says the fetal fraction could not be loaded, and counts nothing as zero', async () => {
      failing('/families/NIPT001/nipt/summary');
      renderPage('NIPT001');

      expect(await screen.findByText('Fetal fraction could not be loaded')).toBeInTheDocument();
      expect(
        screen.getByText(/Could not load the fetal-fraction estimate — this is not an empty result/),
      ).toHaveTextContent(/low-confidence warning, and the filter and category counts, are unknown/);
      // The funnel and the category options read as unknown, not 0.
      expect(screen.queryByText(/Fetal fraction —/)).not.toBeInTheDocument();
      expect(screen.getAllByText(/ —$/).length).toBeGreaterThan(0);
      const categoryCounts = document.querySelectorAll('.nipt-category-option-count');
      expect(categoryCounts.length).toBeGreaterThan(0);
      categoryCounts.forEach((count) => expect(count.textContent).toBe('—'));
    });

    it('says the coverage QC could not be loaded, instead of loading for good', async () => {
      failing('/families/NIPT001/nipt/coverage');
      renderPage('NIPT001');

      expect(await screen.findByText(/Could not load the coverage QC — this is not an empty result/)).toBeInTheDocument();
      expect(screen.queryByText('Loading coverage…')).not.toBeInTheDocument();
    });

    // #610 — a server error is not a family that does not exist.
    it('says the family could not be loaded, not that it was not found', async () => {
      failing('/families/NIPT001');
      renderPage('NIPT001');

      expect(await screen.findByRole('heading', { name: 'Family could not be loaded' })).toBeInTheDocument();
      expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'Back to the dashboard' })).toHaveAttribute('href', '/dashboard');
    });
  });
});
