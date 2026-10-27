import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilyNiptPage from '../FamilyNiptPage';
import { READ_ONLY_HINT } from '../../../lib/familyAccess';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../../../test/familyPageBanners';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
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
            // A track of the plasma's the check did not read as depth, and why (TRACE-9).
            coverage_notes: [
              'CFDNA1: the WisecondorX coverage holds log2 ratios, not read depth; it is not read as depth.',
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
    // Each of the plasma's coverage tracks the check did not read as depth is named, with why.
    expect(screen.getByRole('note', { name: 'Coverage tracks not read as depth' })).toHaveTextContent(
      'CFDNA1: the WisecondorX coverage holds log2 ratios, not read depth; it is not read as depth.',
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

  // The NIPT analysis is signed out through the family sign-out, its gates and report code:
  // one signed record per analysis, which holds the NIPT results (REG-2).
  describe('signing the NIPT analysis out', () => {
    const LATEST = {
      version: 2,
      signed_out_by: 'lab-signer',
      signed_out_at: '2026-10-15T09:00:00Z',
      content_hash: 'hash-v2',
      report_code: '7KQM2X-2-4PHT',
    };

    const serve = ({
      latest = null,
      changed = null,
      notCompared = [],
      projects,
      readOnly = false,
    }: {
      latest?: typeof LATEST | null;
      changed?: string[] | null;
      notCompared?: string[];
      projects?: unknown[];
      readOnly?: boolean;
    } = {}) =>
      apiMock.get.mockImplementation((url: string) => {
        if (url === '/families/NIPT001') {
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              members: [],
              projects: projects ? ['p1'] : [],
              metadata: { analysis_type: 'monogenic_nipt' },
              read_only: readOnly,
            },
          });
        }
        if (url === '/projects') return Promise.resolve({ data: projects ?? [] });
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
        if (url === '/families/NIPT001/report/sign-outs') {
          return Promise.resolve({ data: { family_id: 'NIPT001', latest, signouts: latest ? [latest] : [] } });
        }
        if (url === '/families/NIPT001/report/sign-out-check') {
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              version: latest?.version ?? null,
              content_hash: latest?.content_hash ?? null,
              matches: !changed,
              changed_sections: changed ?? [],
              not_compared: notCompared,
              not_captured: [],
              checked_at: '2026-10-16T09:00:00Z',
            },
          });
        }
        return Promise.resolve({ data: [] });
      });

    // Where a sign-out leads: the signed version, opened on the report page.
    const SignedVersionProbe = () => {
      const location = useLocation();
      const state = location.state as { signedOut?: { version?: number } } | null;
      return (
        <p>
          Report page {location.pathname}
          {location.search} (signed out as version {state?.signedOut?.version ?? 'none'})
        </p>
      );
    };

    const renderWithReport = () =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={['/families/NIPT001/nipt']}>
            <Routes>
              <Route path="/families/:familyId/nipt" element={<FamilyNiptPage />} />
              <Route path="/families/:familyId/report" element={<SignedVersionProbe />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    it('signs out through the family sign-out, and opens the version it signed', async () => {
      serve();
      apiMock.post.mockResolvedValue({ data: { version: 1, report_code: '7KQM2X-1-B3HV' } });
      renderWithReport();

      const sign = await screen.findByRole('button', { name: 'Sign out NIPT analysis' });
      const status = screen.getByRole('region', { name: 'Sign-out' });
      expect(status).toHaveTextContent('This NIPT analysis has not been signed out.');
      expect(status).toHaveTextContent('The family has one signed record');
      fireEvent.click(sign);

      expect(await screen.findByText(/Report page \/families\/NIPT001\/report\?version=1/)).toHaveTextContent(
        '(signed out as version 1)',
      );
      expect(apiMock.post).toHaveBeenCalledWith('/families/NIPT001/report/sign-out', {
        acknowledge_drift: false,
        drift_acknowledgement_reason: undefined,
        acknowledge_qc: false,
        qc_acknowledgement_reason: undefined,
        acknowledge_import_incomplete: false,
        import_incomplete_acknowledgement_reason: undefined,
      });
    });

    it('asks for a reason when a NIPT quality check failed, and freezes it with the sign-out', async () => {
      serve();
      const concern =
        'Sample CFDNA1 (maternal plasma): male DNA (chrY at the autosomal level, chrX low): this sample cannot be maternal plasma.';
      apiMock.post
        .mockRejectedValueOnce({
          response: {
            status: 409,
            data: {
              detail: {
                gate: 'sample_qc',
                message:
                  'A NIPT quality check failed. Resolve each concern listed, or acknowledge them with a reason to sign out anyway.',
                qc_summary: { overall_status: 'pass', messages: [concern] },
                failed_checks: [],
                unverifiable_checks: [],
                nipt_failed_checks: [concern],
                warning_checks: [],
              },
            },
          },
        })
        .mockResolvedValueOnce({ data: { version: 1 } });
      renderWithReport();

      fireEvent.click(await screen.findByRole('button', { name: 'Sign out NIPT analysis' }));
      const dialog = await screen.findByRole('dialog', { name: 'Sample-integrity QC acknowledgement required' });
      expect(dialog).toHaveTextContent('A NIPT quality check failed');
      expect(within(dialog).getByText(concern)).toBeInTheDocument();
      fireEvent.change(within(dialog).getByLabelText(/Reason for signing out despite the QC concern/), {
        target: { value: 'Plasma re-drawn; identity confirmed.' },
      });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out anyway' }));

      expect(await screen.findByText(/Report page \/families\/NIPT001\/report\?version=1/)).toBeInTheDocument();
      expect(apiMock.post).toHaveBeenLastCalledWith(
        '/families/NIPT001/report/sign-out',
        expect.objectContaining({ acknowledge_qc: true, qc_acknowledgement_reason: 'Plasma re-drawn; identity confirmed.' }),
      );
    });

    it('lists the checks that could not run beside the failures, each group under its heading (CLIN-58)', async () => {
      // The mother's sex check failed and the maternal plasma is male DNA, while the paternity
      // and the maternal lineage could not run. The dialog listed the failures and the
      // paternity (`qc_summary`), but not the maternal lineage, which the 409 names among the
      // checks that could not run: the reason written there might not cover it. Each group the
      // 409 names is listed under its heading, before the reason.
      serve();
      const motherFailed =
        'Sample MOTHER1: recorded female but genotypes indicate male (1.0% chrX het over 500 sites) — possible sample swap or mislabel.';
      const paternity =
        'Sample FATHER1 (father): too few paternal-informative sites (3 homozygous, 0 het) to assess paternity.';
      const lineage =
        'NIPT maternal lineage could not be verified (too few maternal-informative sites) — possible wrong mother.';
      const plasma =
        'Sample CFDNA1 (maternal plasma): male DNA (chrY at the autosomal level, chrX low): this sample cannot be maternal plasma.';
      const fetalSex =
        'Fetal sex indeterminate — too few informative paternal-X sites (0 paternal-X transmitted, 0 absent of 0 sites).';
      apiMock.post
        .mockRejectedValueOnce({
          response: {
            status: 409,
            data: {
              detail: {
                gate: 'sample_qc',
                message:
                  "Sample-integrity QC status is 'fail' (possible sample or pedigree swap — TF-06 H4). A sample-integrity check that confirms the pedigree or a sample's identity could not run. A sample swap can present as missing data rather than a mismatch. A NIPT quality check failed. Resolve each concern listed, or acknowledge them with a reason to sign out anyway.",
                qc_summary: {
                  overall_status: 'fail',
                  messages: [motherFailed, paternity, fetalSex, plasma],
                },
                failed_checks: [motherFailed],
                unverifiable_checks: [paternity, lineage],
                nipt_failed_checks: [plasma],
                warning_checks: [fetalSex],
              },
            },
          },
        })
        .mockResolvedValueOnce({ data: { version: 1 } });
      renderWithReport();

      fireEvent.click(await screen.findByRole('button', { name: 'Sign out NIPT analysis' }));
      const dialog = await screen.findByRole('dialog', { name: 'Sample-integrity QC acknowledgement required' });
      expect(within(dialog).getByText(lineage)).toBeInTheDocument();
      const group = (name: string) =>
        within(within(dialog).getByRole('list', { name }))
          .getAllByRole('listitem')
          .map((item) => item.textContent);
      expect(group('Sample QC checks that failed')).toEqual([motherFailed]);
      expect(group('Checks that could not run')).toEqual([paternity, lineage]);
      expect(group('NIPT quality checks that failed')).toEqual([plasma]);
      expect(group('Warnings, which do not stop the sign-out on their own')).toEqual([fetalSex]);
      // Each concern once, the paternity among the checks that could not run only.
      expect(within(dialog).getAllByRole('listitem')).toHaveLength(5);
      const reason = within(dialog).getByLabelText(
        'Reason for signing out despite the QC concerns listed (required)',
      );
      for (const list of within(dialog).getAllByRole('list')) {
        expect(list.compareDocumentPosition(reason) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
      }
      fireEvent.change(reason, { target: { value: 'Samples re-drawn and re-run; identities confirmed.' } });
      fireEvent.click(within(dialog).getByRole('button', { name: 'Sign out anyway' }));

      expect(await screen.findByText(/Report page \/families\/NIPT001\/report\?version=1/)).toBeInTheDocument();
      expect(apiMock.post).toHaveBeenLastCalledWith(
        '/families/NIPT001/report/sign-out',
        expect.objectContaining({
          acknowledge_qc: true,
          qc_acknowledgement_reason: 'Samples re-drawn and re-run; identities confirmed.',
        }),
      );
    });

    it('names the latest signed version, its report code, and what changed since', async () => {
      serve({ latest: LATEST, changed: ['nipt'] });
      renderWithReport();

      expect(await screen.findByRole('button', { name: 'Amend sign-out' })).toBeEnabled();
      const status = screen.getByRole('region', { name: 'Sign-out' });
      await waitFor(() => expect(status).toHaveTextContent('Changed since sign-out: NIPT results.'));
      expect(status).toHaveTextContent(
        'Signed version 2 (report code 7KQM2X-2-4PHT) was signed out by lab-signer on 2026-10-15 09:00 UTC.',
      );
      expect(within(status).getByRole('link', { name: 'View signed version 2' })).toHaveAttribute(
        'href',
        '/families/NIPT001/report?version=2',
      );
    });

    it('names what the check could not compare with the latest signed version (TRACE-19)', async () => {
      serve({ latest: LATEST, notCompared: ['nipt'] });
      renderWithReport();

      expect(await screen.findByRole('button', { name: 'Amend sign-out' })).toBeEnabled();
      const status = screen.getByRole('region', { name: 'Sign-out' });
      await waitFor(() =>
        expect(status).toHaveTextContent(
          'The family’s current data still matches it in everything it holds. Not in signed version 2, so not compared: NIPT results.',
        ),
      );
    });

    // SEC-1: a reader in the family's project reads the NIPT analysis and whether it was signed
    // out, but signs nothing out: the button is disabled with the reason the report page gives.
    it('offers a reader no sign-out, says why, and sends none', async () => {
      serve({ readOnly: true });
      renderWithReport();

      const sign = await screen.findByRole('button', { name: 'Sign out NIPT analysis' });
      await waitFor(() => expect(sign).toBeDisabled());
      expect(sign).toHaveAttribute('title', READ_ONLY_HINT);
      fireEvent.click(sign);
      expect(screen.getByRole('region', { name: 'Sign-out' })).toHaveTextContent(
        'This NIPT analysis has not been signed out.',
      );
      expect(apiMock.post).not.toHaveBeenCalled();
    });

    it('offers no sign-out off the validated assembly scope', async () => {
      serve({ projects: [offScopeProject('p1')] });
      renderWithReport();

      const sign = await screen.findByRole('button', { name: 'Sign out NIPT analysis' });
      await waitFor(() => expect(sign).toBeDisabled());
      expect(sign).toHaveAttribute('title', 'Not validated for clinical use — this analysis cannot be signed out');
    });
  });

  // UI-4: a NIPT sign-out freezes the variants tagged report, and the NIPT page could not list
  // them. It takes the small-variant list's review-tag filter, and "Show reported" lists what a
  // sign-out freezes.
  describe('the review-tag filter and the reported variants', () => {
    const TAGS = [
      { key: 'excluded', label: 'Excluded', group: 'collaboration', color: '#6b7280', sort_order: 20, scope: 'system', is_custom: false },
      { key: 'report', label: 'Report', group: 'collaboration', color: '#16a34a', sort_order: 30, scope: 'system', is_custom: false },
    ];
    const REPORTED = {
      _id: '1-1001-A-G',
      chr: '1',
      start: 1001,
      end: 1001,
      type: 'SNV',
      ref: 'A',
      alt: 'G',
      gene: 'GENEDN',
      genotypes: [],
      review: { variant_id: '1-1001-A-G', classification: null, tags: ['report'], note: null, updated_at: '2026-10-20T09:00:00Z' },
      nipt: {
        category: 1,
        category_label: 'de novo in fetus',
        maternal_state: 'hom_ref',
        fetal_inheritance: 'de_novo',
        expected_vaf: 0.06,
        observed_vaf: 0.06,
        confidence: 0.98,
        flags: [],
      },
    };
    const ARTIFACT = { variant_id: '1-9501-A-G', reason: 'artifact_list' };

    const serve = ({ reported = 2, presets = [] }: { reported?: number; presets?: unknown[] } = {}) => {
      apiMock.get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) => {
        if (url === '/families/NIPT001/small-variant-filter-presets') return Promise.resolve({ data: presets });
        if (url === '/families/NIPT001') {
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              members: [
                { sample_id: 'CFDNA', role: 'mother', active: true },
                { sample_id: 'FATHER', role: 'father', active: true },
              ],
              metadata: { analysis_type: 'monogenic_nipt' },
            },
          });
        }
        if (url === '/families/NIPT001/nipt/summary') {
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              fetal_fraction: FETAL_FRACTION,
              category_counts: { '1': 1 },
              filter_counts: { total_in: 10, failed_quality: 0, failed_artifact: 1, passed: 9 },
            },
          });
        }
        if (url === '/families/NIPT001/small-variant-tags') return Promise.resolve({ data: TAGS });
        if (url === '/families/NIPT001/small-variant-review-summary') {
          return Promise.resolve({
            data: { reviewed_variant_count: 3, note_count: 0, tag_counts: { report: reported, excluded: 1 } },
          });
        }
        if (url === '/families/NIPT001/nipt/variants') {
          const tags = (config?.params?.review_tag as string[] | undefined) ?? [];
          const reportedView = tags.includes('report');
          return Promise.resolve({
            data: {
              family_id: 'NIPT001',
              total: reportedView ? 1 : 0,
              fetal_fraction: FETAL_FRACTION,
              variants: reportedView ? [REPORTED] : [],
              not_classified: reportedView ? [ARTIFACT] : [],
            },
          });
        }
        return Promise.resolve({ data: [] });
      });
    };

    const renderAt = (path: string) =>
      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[path]}>
            <Routes>
              <Route path="/families/:familyId/nipt" element={<FamilyNiptPage />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

    const variantSearches = () =>
      apiMock.get.mock.calls
        .filter(([url]) => url === '/families/NIPT001/nipt/variants')
        .map(([, config]) => (config?.params ?? {}) as Record<string, unknown>);

    const reviewSection = async () =>
      (await screen.findByText('Review and curation')).closest('details') as HTMLElement;

    it('sends the review tags and the excluded tags, by the small-variant list’s names', async () => {
      serve();
      renderAt('/families/NIPT001/nipt?review_tag=report&exclude_review_tag=excluded');

      await waitFor(() => expect(variantSearches().length).toBeGreaterThan(0));
      const [params] = variantSearches();
      expect(params.review_tag).toEqual(['report']);
      expect(params.exclude_review_tag).toEqual(['excluded']);
      // Chipped as the small-variant page chips them, and now applied.
      expect(await screen.findByRole('button', { name: 'Review tags: Report' })).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Exclude review tags: Excluded' })).toBeInTheDocument();
    });

    it('offers the review tags, but no classification or saved-notes filter the list does not apply', async () => {
      serve();
      renderAt('/families/NIPT001/nipt');

      const section = await reviewSection();
      expect(within(section).getByRole('checkbox', { name: 'Report' })).toBeInTheDocument();
      expect(within(section).queryByText('Classification')).not.toBeInTheDocument();
      expect(within(section).queryByRole('checkbox', { name: 'Pathogenic - class 5' })).not.toBeInTheDocument();
      expect(within(section).queryByRole('option', { name: 'P/LP/VUS' })).not.toBeInTheDocument();
      expect(within(section).queryByText('Only show variants with saved notes')).not.toBeInTheDocument();

      // Within the view it opened on: the reported de novo candidates.
      fireEvent.click(within(section).getByRole('checkbox', { name: 'Report' }));
      fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));
      await waitFor(() => expect(variantSearches().at(-1)?.review_tag).toEqual(['report']));
      expect(variantSearches().at(-1)?.inheritance).toBe('de_novo');
    });

    // ENG-19: a saved search, made on the small-variant list, filtered on what the NIPT list does
    // not apply, and its chips showed as if it did.
    it('shows no chip for a saved search’s filters the list does not apply, and says once which they are', async () => {
      serve({
        presets: [
          {
            _id: 'preset-1',
            name: 'Reported, classified',
            filters: {
              review_tags: ['report'],
              classification: ['Pathogenic - class 5', 'Likely Pathogenic - class 4'],
              has_notes: true,
              prioritize: true,
            },
            sample_filters: { FATHER: { gt: ['0/1', '1/0', '0|1', '1|0'], qual: '20', dp: '', af: '', ad_alt: '' } },
            sample_templates: {},
          },
        ],
      });
      renderAt('/families/NIPT001/nipt');

      // The De novo preset it opened on carries none.
      await screen.findByRole('option', { name: 'Reported, classified' });
      expect(screen.queryByText(/The NIPT list does not apply/)).not.toBeInTheDocument();

      fireEvent.change(screen.getByLabelText('Preset or saved search'), { target: { value: 'saved:preset-1' } });
      fireEvent.click(screen.getByRole('button', { name: 'Apply filters' }));

      await waitFor(() => expect(variantSearches().at(-1)?.review_tag).toEqual(['report']));
      const params = variantSearches().at(-1) ?? {};
      for (const key of ['classification', 'has_notes', 'prioritize', 'sample_filter']) {
        expect(params).not.toHaveProperty(key);
      }
      // Only the review tag it applies is chipped and counted.
      expect(await screen.findByRole('button', { name: 'Review tags: Report' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Classification/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Has notes' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Phenotype prioritization' })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^FATHER/ })).not.toBeInTheDocument();
      expect(screen.getByText('Active filters 1')).toBeInTheDocument();
      // Said once, beside the chips.
      expect(
        screen.getAllByText(
          'The NIPT list does not apply this search’s phenotype-prioritization, classification, saved-notes and per-sample filters.',
        ),
      ).toHaveLength(1);
    });

    it('shows no chip for a classification or saved-notes filter a link carries, and says so', async () => {
      serve();
      renderAt(
        '/families/NIPT001/nipt?review_tag=report&classification=Pathogenic%20-%20class%205&has_notes=true&prioritize=false',
      );

      expect(await screen.findByRole('button', { name: 'Review tags: Report' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /^Classification/ })).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Has notes' })).not.toBeInTheDocument();
      expect(
        screen.getByText('The NIPT list does not apply this search’s classification and saved-notes filters.'),
      ).toBeInTheDocument();
    });

    it('lists what a sign-out freezes with Show reported, and names what the analysis does not classify', async () => {
      serve();
      renderAt('/families/NIPT001/nipt');

      const signOut = await screen.findByRole('region', { name: 'Sign-out' });
      const show = await within(signOut).findByRole('button', { name: 'Show reported (2)' });
      expect(signOut).toHaveTextContent(
        'Show reported (2) lists the variants tagged report, with every other filter cleared: those a sign-out would freeze.',
      );
      // The page opened on the De novo preset.
      expect(variantSearches()[0].inheritance).toBe('de_novo');

      fireEvent.click(show);

      await waitFor(() => expect(variantSearches().at(-1)?.review_tag).toEqual(['report']));
      // Every other filter cleared: no view, no category, no consequence or frequency filter.
      const params = variantSearches().at(-1) ?? {};
      for (const key of ['inheritance', 'category', 'min_confidence', 'impact', 'max_gnomad_af', 'max_gnomad_popmax_af', 'exclude_review_tag']) {
        expect(params).not.toHaveProperty(key);
      }
      expect(await screen.findByRole('button', { name: 'Review tags: Report' })).toBeInTheDocument();
      expect(screen.getByLabelText('NIPT inheritance preset')).toHaveValue('');
      // The reported variant, and the reported artifact the analysis leaves out, named with
      // the reason the signed record gives.
      expect((await screen.findAllByText('GENEDN')).length).toBeGreaterThan(0);
      const notClassified = screen.getByRole('status', { name: 'Tagged variants not classified' });
      expect(notClassified).toHaveTextContent(
        '1 variant with the selected review tags is not in this list: the analysis does not classify it. ' +
          'A sign-out names a reported one as not classified, with why.',
      );
      expect(notClassified).toHaveTextContent(
        '1-9501-A-G: not classified — it is on the assay’s recurrent-artifact list, which the analysis leaves out.',
      );
    });

    it('counts the reported variants again once a tag is saved', async () => {
      serve();
      apiMock.put.mockResolvedValue({
        data: { ...REPORTED.review, tags: [] },
      });
      renderAt('/families/NIPT001/nipt?review_tag=report');

      await screen.findByRole('button', { name: 'Show reported (2)' });
      const summaryReads = () =>
        apiMock.get.mock.calls.filter(([url]) => url === '/families/NIPT001/small-variant-review-summary').length;
      const before = summaryReads();
      serve({ reported: 1 });
      const [untag] = (await screen.findAllByRole('button', { name: 'Report' })).filter(
        (button) => button.getAttribute('aria-pressed') === 'true',
      );
      fireEvent.click(untag);

      expect(await screen.findByRole('button', { name: 'Show reported (1)' })).toBeInTheDocument();
      expect(summaryReads()).toBeGreaterThan(before);
      expect(apiMock.put).toHaveBeenCalledWith(
        '/families/NIPT001/small-variants/1-1001-A-G/review',
        expect.objectContaining({ tags: [] }),
      );
    });

    it('says the count is unknown, not zero, while the review summary is not in', async () => {
      serve();
      const served = apiMock.get.getMockImplementation();
      apiMock.get.mockImplementation((url: string, config?: { params?: Record<string, unknown> }) =>
        url === '/families/NIPT001/small-variant-review-summary'
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : served?.(url, config),
      );
      renderAt('/families/NIPT001/nipt');

      expect(await screen.findByRole('button', { name: 'Show reported' })).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: /Show reported \(/ })).not.toBeInTheDocument();
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
