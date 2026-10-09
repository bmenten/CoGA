import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import FamilySampleQcPage from '../FamilySampleQcPage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../../../lib/api', () => ({ default: apiMock }));

const TRIO_FAMILY = {
  family_id: 'FAM1',
  members: [
    { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
    { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
    { sample_id: 'CHILD', role: 'proband', affected: true, sex: 'male' },
  ],
  relationships: [
    { id: 'r1', relationship_type: 'parent_child', sample_id_a: 'FATHER', sample_id_b: 'CHILD', role_a: 'father' },
    { id: 'r2', relationship_type: 'parent_child', sample_id_a: 'MOTHER', sample_id_b: 'CHILD', role_a: 'mother' },
  ],
  pedigree: null,
};

const mockApi = (qcData: unknown, family: unknown = TRIO_FAMILY) => {
  apiMock.get.mockImplementation((url: string) => {
    if (url === '/families/FAM1/qc/sample-integrity') return Promise.resolve({ data: qcData });
    if (url === '/families/FAM1') return Promise.resolve({ data: family });
    return Promise.resolve({ data: {} });
  });
};

const renderPage = () => {
  const queryClient = createTestQueryClient();
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/families/FAM1/qc']}>
        <Routes>
          <Route path="/families/:familyId/qc" element={<FamilySampleQcPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

// The member's row in the per-sample table.
const memberRow = (sampleId: string) =>
  screen.getByText(sampleId, { selector: '.family-member-name-button' }).closest('tr') as HTMLElement;

const genotypeSexCell = (sampleId: string) =>
  memberRow(sampleId).querySelector('.qc-genotype-sex') as HTMLElement;

// The pedigree symbol's tooltip (its SVG <title>).
const pedigreeTooltip = (container: HTMLElement, sampleId: string) =>
  container.querySelector(`[data-pedigree-node="${sampleId}"] title`)?.textContent;

describe('FamilySampleQcPage', () => {
  it('draws the pedigree with failed QC rings, a colour-coded table and a relatedness matrix', async () => {
    mockApi({
      family_id: 'FAM1',
      overall_status: 'fail',
      application: 'wgs',
      application_label: 'Long-read WGS family',
      application_summary: 'Full pedigree QC on the SNV call set.',
      genotype_source: 'clair3',
      paternity_check: null,
      autosomal_sites: 90000,
      notes: [],
      sex_checks: [
        {
          sample_id: 'CHILD',
          recorded_sex: 'male',
          inferred_sex: 'female',
          x_het_rate: 0.3,
          x_sites: 500,
          status: 'fail',
          message: 'Recorded male but genotypes indicate female — possible sample swap.',
        },
      ],
      relatedness_checks: [
        {
          sample_a: 'CHILD',
          sample_b: 'FATHER',
          expected_relationship: 'parent-child',
          inferred_relationship: 'unrelated',
          kinship: 0.01,
          ibs0_rate: 0.18,
          informative_sites: 80000,
          status: 'fail',
          message: 'Recorded parent-child but genotypes look unrelated.',
        },
        {
          sample_a: 'FATHER',
          sample_b: 'MOTHER',
          expected_relationship: 'unrelated',
          inferred_relationship: 'third-degree',
          kinship: 0.03,
          ibs0_rate: 0.12,
          informative_sites: 80000,
          status: 'warn',
          message: 'Parents look third-degree — possible consanguinity.',
        },
      ],
      mendelian_checks: [
        {
          child: 'CHILD',
          parents: ['FATHER', 'MOTHER'],
          informative_sites: 80000,
          mendel_errors: 12000,
          mendel_rate: 0.15,
          status: 'fail',
          message: 'High Mendelian-error rate — likely swap or wrong parent.',
        },
      ],
    });

    const { container } = renderPage();

    expect(await screen.findByRole('heading', { name: /family FAM1/i })).toBeInTheDocument();
    // Pedigree renders and rings CHILD red (failed sex + Mendelian + relatedness).
    expect(screen.getByText('Pedigree & sample integrity')).toBeInTheDocument();
    await waitFor(() =>
      expect(
        container.querySelector('[data-pedigree-node="CHILD"][data-qc-status="fail"]'),
      ).toBeTruthy(),
    );
    // Sample table colour-codes the genotype sex mismatch and shows the Mendelian rate.
    expect(screen.getByText('Family members & per-sample checks')).toBeInTheDocument();
    const mismatch = container.querySelector('.qc-genotype-sex--mismatch');
    expect(mismatch?.textContent).toBe('female ✗');
    expect(container.querySelector('.qc-mendel--fail')?.textContent).toMatch(/15\.00%/);
    // Relatedness association matrix renders the observed relationship, flagged as an error.
    expect(screen.getByText('Relatedness vs pedigree')).toBeInTheDocument();
    // Symmetric matrix: the observed relationship shows in both mirror cells.
    expect(screen.getAllByText('unrelated').length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector('.qc-matrix-cell--fail')).toBeTruthy();
    // A warning (here, co-parents who look related) is outlined amber, not red, and the
    // page says which outline means what.
    expect(container.querySelector('.qc-matrix-cell--warn')).toBeTruthy();
    expect(screen.getByText(/are outlined: red for a fail, amber for a warning\./)).toBeInTheDocument();
  });

  it('opens with the shared family header', async () => {
    renderPage();
    // Same top card as every other family page: the title is the way back, and there is
    // no separate "Back to family" button doing the same job.
    const title = await screen.findByRole('link', { name: 'Family FAM1' });
    expect(title).toHaveAttribute('href', '/families/FAM1');
    expect(screen.queryByRole('link', { name: /back to family/i })).not.toBeInTheDocument();
  });

  it('shows an all-clear overall status and green rings when checks pass', async () => {
    mockApi({
      family_id: 'FAM1',
      overall_status: 'pass',
      application: 'wgs',
      application_label: 'Long-read WGS family',
      application_summary: 'Full pedigree QC on the SNV call set.',
      genotype_source: 'clair3',
      paternity_check: null,
      autosomal_sites: 90000,
      notes: [],
      sex_checks: [
        {
          sample_id: 'CHILD',
          recorded_sex: 'male',
          inferred_sex: 'male',
          x_het_rate: 0.01,
          x_sites: 500,
          status: 'pass',
          message: 'Genotype sex matches the record.',
        },
      ],
      relatedness_checks: [],
      mendelian_checks: [],
    });

    const { container } = renderPage();

    expect(await screen.findByText(/All sample-integrity checks passed/)).toBeInTheDocument();
    await waitFor(() =>
      expect(
        container.querySelector('[data-pedigree-node="CHILD"][data-qc-status="pass"]'),
      ).toBeTruthy(),
    );
    expect(container.querySelector('.qc-genotype-sex--match')?.textContent).toBe('male ✓');
    expect(pedigreeTooltip(container, 'CHILD')).toBe('Sex male (matches record)');
  });

  it('never marks a sex the genotypes could not confirm as a match (TF-06 H4)', async () => {
    const noChrX = 'No chrX genotypes available for sex inference.';
    const indeterminate = 'Could not infer sex (8.0% chrX het over 500 sites).';
    const notRecorded = 'Sex not recorded; genotypes indicate male (1.0% chrX het over 500 sites).';
    mockApi(
      {
        family_id: 'FAM1',
        overall_status: 'warn',
        application: 'wgs',
        application_label: 'Long-read WGS family',
        application_summary: 'Full pedigree QC on the SNV call set.',
        genotype_source: 'clair3',
        paternity_check: null,
        autosomal_sites: 90000,
        notes: [],
        sex_checks: [
          {
            sample_id: 'FATHER',
            recorded_sex: 'male',
            inferred_sex: 'indeterminate',
            x_het_rate: null,
            x_sites: 0,
            status: 'skip',
            message: noChrX,
          },
          {
            sample_id: 'MOTHER',
            recorded_sex: 'female',
            inferred_sex: 'indeterminate',
            x_het_rate: 0.08,
            x_sites: 500,
            status: 'warn',
            message: indeterminate,
          },
          {
            sample_id: 'CHILD',
            recorded_sex: 'unknown',
            inferred_sex: 'male',
            x_het_rate: 0.01,
            x_sites: 500,
            status: 'warn',
            message: notRecorded,
          },
        ],
        // The father's parent-child check passes, but his sex could not be checked.
        relatedness_checks: [
          {
            sample_a: 'CHILD',
            sample_b: 'FATHER',
            expected_relationship: 'parent-child',
            inferred_relationship: 'parent-child',
            kinship: 0.25,
            ibs0_rate: 0.001,
            informative_sites: 80000,
            status: 'pass',
            message: 'Relationship matches the pedigree.',
          },
        ],
        mendelian_checks: [],
      },
      {
        ...TRIO_FAMILY,
        members: [
          { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
          { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
          { sample_id: 'CHILD', role: 'proband', affected: true, sex: 'unknown' },
        ],
      },
    );

    const { container } = renderPage();

    expect(await screen.findByText('Family members & per-sample checks')).toBeInTheDocument();
    // No chrX genotypes: not checked, a neutral mark.
    expect(genotypeSexCell('FATHER')).toHaveClass('qc-genotype-sex--unchecked');
    expect(genotypeSexCell('FATHER')).toHaveTextContent('indeterminate ?');
    // Genotypes too ambiguous to sex, and no recorded sex to compare with: not confirmed.
    expect(genotypeSexCell('MOTHER')).toHaveClass('qc-genotype-sex--unconfirmed');
    expect(genotypeSexCell('MOTHER')).toHaveTextContent('indeterminate !');
    expect(genotypeSexCell('CHILD')).toHaveClass('qc-genotype-sex--unconfirmed');
    expect(genotypeSexCell('CHILD')).toHaveTextContent('male !');
    // Each cell's tooltip is the check's own message.
    expect(genotypeSexCell('FATHER')).toHaveAccessibleName(noChrX);
    expect(genotypeSexCell('MOTHER')).toHaveAccessibleName(indeterminate);
    expect(genotypeSexCell('CHILD')).toHaveAccessibleName(notRecorded);
    expect(container.querySelector('.qc-genotype-sex--match')).toBeNull();
    expect(container.querySelector('.family-members-table')?.textContent).not.toMatch('✓');

    // The rings say why, and none of them claims a match.
    await waitFor(() =>
      expect(
        container.querySelector('[data-pedigree-node="MOTHER"][data-qc-status="warn"]'),
      ).toBeTruthy(),
    );
    expect(pedigreeTooltip(container, 'MOTHER')).toBe(indeterminate);
    expect(pedigreeTooltip(container, 'CHILD')).toBe(notRecorded);
    // A check that could not run counts as a warning, so the passing parent-child check
    // does not turn the father's ring green.
    expect(container.querySelector('[data-pedigree-node="FATHER"]')).toHaveAttribute(
      'data-qc-status',
      'warn',
    );
    expect(pedigreeTooltip(container, 'FATHER')).toBe(noChrX);
    expect(within(memberRow('MOTHER')).getByText('Warning')).toBeInTheDocument();
    expect(container.innerHTML).not.toMatch(/matches record/);
  });

  describe('a sex check that could not run (CLIN-1)', () => {
    const noChrX = 'No chrX genotypes available for sex inference.';
    const unchecked =
      'Sex could not be checked for sample FATHER. A check that could not run counts as a warning, not a pass.';
    const passingSex = (sampleId: string, sex: string) => ({
      sample_id: sampleId,
      recorded_sex: sex,
      inferred_sex: sex,
      x_het_rate: sex === 'male' ? 0.01 : 0.3,
      x_sites: 500,
      status: 'pass',
      message: 'Genotype-inferred sex matches the record.',
    });
    const parentChild = (parent: string) => ({
      sample_a: 'CHILD',
      sample_b: parent,
      expected_relationship: 'parent-child',
      inferred_relationship: 'parent-child',
      kinship: 0.25,
      ibs0_rate: 0.001,
      informative_sites: 80000,
      status: 'pass',
      message: 'Confirmed parent-child.',
    });
    // Every check passes but the father's sex check, which had no chrX genotypes to read.
    const qc = (overall: 'pass' | 'warn', notes: string[]) => ({
      family_id: 'FAM1',
      overall_status: overall,
      application: 'wgs',
      application_label: 'Long-read WGS family',
      application_summary: 'Full pedigree QC on the SNV call set.',
      genotype_source: 'clair3',
      paternity_check: null,
      autosomal_sites: 90000,
      notes,
      sex_checks: [
        {
          sample_id: 'FATHER',
          recorded_sex: 'male',
          inferred_sex: 'indeterminate',
          x_het_rate: null,
          x_sites: 0,
          status: 'skip',
          message: noChrX,
        },
        passingSex('MOTHER', 'female'),
        passingSex('CHILD', 'male'),
      ],
      relatedness_checks: [parentChild('FATHER'), parentChild('MOTHER')],
      mendelian_checks: [
        {
          child: 'CHILD',
          parents: ['FATHER', 'MOTHER'],
          informative_sites: 80000,
          mendel_errors: 40,
          mendel_rate: 0.0005,
          status: 'pass',
          message: 'Mendelian-error rate within tolerance.',
        },
      ],
    });

    it('reads a warning that names the sample, never "all checks passed"', async () => {
      mockApi(qc('warn', [unchecked]));
      const { container } = renderPage();

      expect(await screen.findByText(unchecked)).toBeInTheDocument();
      const overall = container.querySelector('.qc-overall') as HTMLElement;
      expect(overall).toHaveClass('qc-overall--warn');
      expect(within(overall).getByText('Warning')).toBeInTheDocument();
      expect(overall).toHaveTextContent('Some checks need attention');
      expect(container).not.toHaveTextContent(/All sample-integrity checks passed/);

      // The father's own verdict is a warning too, ring and table alike; the samples whose
      // every check passed stay green.
      await waitFor(() =>
        expect(container.querySelector('[data-pedigree-node="FATHER"]')).toHaveAttribute(
          'data-qc-status',
          'warn',
        ),
      );
      expect(pedigreeTooltip(container, 'FATHER')).toBe(noChrX);
      expect(within(memberRow('FATHER')).getByText('Warning')).toBeInTheDocument();
      expect(within(memberRow('FATHER')).queryByText('Pass')).not.toBeInTheDocument();
      for (const sampleId of ['MOTHER', 'CHILD']) {
        expect(container.querySelector(`[data-pedigree-node="${sampleId}"]`)).toHaveAttribute(
          'data-qc-status',
          'pass',
        );
        expect(within(memberRow(sampleId)).getByText('Pass')).toBeInTheDocument();
      }
    });

    it('shows a warning even when the verdict it is given is a pass', async () => {
      // A verdict rolled up before a check that could not run counted as a warning.
      mockApi(qc('pass', []));
      const { container } = renderPage();

      expect(await screen.findByText('Family members & per-sample checks')).toBeInTheDocument();
      const overall = container.querySelector('.qc-overall') as HTMLElement;
      expect(overall).toHaveClass('qc-overall--warn');
      expect(within(overall).getByText('Warning')).toBeInTheDocument();
      expect(container).not.toHaveTextContent(/All sample-integrity checks passed/);
      expect(within(memberRow('FATHER')).getByText('Warning')).toBeInTheDocument();
    });
  });

  it('adapts to NIPT: paternity, fetal sex, parent sex + category QC, no relatedness matrix', async () => {
    mockApi(
      {
        family_id: 'FAM1',
        overall_status: 'pass',
        application: 'nipt',
        application_label: 'Monogenic NIPT (cfDNA)',
        application_summary: 'Paternity is confirmed from paternal-transmitted sites (categories 7/8).',
        genotype_source: 'vardict',
        sex_checks: [
          {
            sample_id: 'FATHER',
            recorded_sex: 'male',
            inferred_sex: 'male',
            x_het_rate: 0.01,
            x_sites: 400,
            status: 'pass',
            message: 'Genotype sex matches the record.',
          },
          {
            sample_id: 'MOTHER',
            recorded_sex: 'female',
            inferred_sex: 'female',
            x_het_rate: 0.3,
            x_sites: 400,
            status: 'pass',
            message: 'Genotype sex matches the record.',
          },
        ],
        relatedness_checks: [],
        mendelian_checks: [],
        paternity_check: {
          father: 'FATHER',
          cat7_transmitted: 90,
          cat8_absent: 1,
          informative_sites: 151,
          hom_alt_transmitted: 40,
          hom_alt_not_transmitted: 1,
          het_transmitted: 50,
          het_not_transmitted: 60,
          status: 'pass',
          message: 'Paternity supported.',
        },
        fetal_sex_check: {
          inferred_sex: 'female',
          x_transmitted: 12,
          x_not_transmitted: 0,
          informative_sites: 12,
          status: 'pass',
          message: 'Fetal sex appears female: paternal X transmitted.',
        },
        category_qc_check: {
          denovo: 1,
          paternal_absent: 2,
          maternal_informative: 60,
          maternal_inherited: 30,
          maternal_inherited_rate: 0.5,
          status: 'pass',
          message: 'Category distribution within expectation.',
        },
        autosomal_sites: 0,
        notes: [],
      },
      {
        family_id: 'FAM1',
        members: [
          { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
          { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
          { sample_id: 'CFDNA', role: 'proband', affected: false, sex: 'unknown' },
        ],
        relationships: [],
        pedigree: null,
      },
    );

    renderPage();

    expect(await screen.findByText("Paternity (the father's alleles in the cfDNA)")).toBeInTheDocument();
    expect(
      screen.getByText(/Father FATHER — homozygous alleles seen 40 of 41 \(all expected\), het alleles seen 50 of 110/),
    ).toBeInTheDocument();
    expect(screen.getByText('Fetal sex (paternal X transmission)')).toBeInTheDocument();
    expect(screen.getByText(/Fetus appears female — 12 paternal-X transmitted/)).toBeInTheDocument();
    // Parents are now sexed (X zygosity), so the per-sample table renders; the
    // cfDNA category QC card shows the maternal transmission rate.
    expect(screen.getByText('Family members & per-sample checks')).toBeInTheDocument();
    expect(screen.getByText('cfDNA category QC')).toBeInTheDocument();
    expect(screen.getByText(/Maternal transmission 50%/)).toBeInTheDocument();
    // No genotype relatedness matrix for the cfDNA application.
    expect(screen.queryByText('Relatedness vs pedigree')).not.toBeInTheDocument();
  });
});
