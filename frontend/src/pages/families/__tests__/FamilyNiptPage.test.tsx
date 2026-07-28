import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyNiptPage from '../FamilyNiptPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

const FETAL_FRACTION = {
  ff: 0.1,
  ff_computed: 0.1,
  ff_external: null,
  ff_median: 0.1,
  ci_low: 0.095,
  ci_high: 0.105,
  n_sites: 40,
  method: 'category7_pooled',
  low_confidence: false,
  disagreement: false,
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

    // Per-gene coverage QC flags the one panel gene below the depth threshold
    // (a compact list, not the full per-region table).
    expect(screen.getByText(/1 of 2 panel genes below QC/)).toBeInTheDocument();
    expect(screen.getByText(/ARID1B · 8x median/)).toBeInTheDocument();

    // The variant renders through the reused small-variant card (≤100 results →
    // cards view), with the NIPT classification block layered on. (BRCA1 appears
    // in the gene link and external-resource links, hence findAllByText.)
    expect((await screen.findAllByText('BRCA1')).length).toBeGreaterThan(0);
    expect(screen.getByText(/paternal, transmitted to fetus/)).toBeInTheDocument();
    expect(screen.getByText('Category 7')).toBeInTheDocument();
    expect(screen.getByText('Page 1 of 1')).toBeInTheDocument();

    // Picking an inheritance preset ticks its matching category checkbox
    // (paternal dominant → category 7).
    const cat7 = screen.getByRole('checkbox', { name: /7 — Paternal, transmitted/ });
    expect(cat7).not.toBeChecked();
    fireEvent.change(screen.getByLabelText('NIPT inheritance preset'), {
      target: { value: 'paternal_dominant' },
    });
    await waitFor(() => expect(cat7).toBeChecked());

    // The category selection survives "Apply filters" (it round-trips through
    // the URL rather than being cleared by the URL-sync effect).
    fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
    await waitFor(() =>
      expect(screen.getByRole('checkbox', { name: /7 — Paternal, transmitted/ })).toBeChecked(),
    );
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
});
