import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
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
    expect(mismatch?.textContent).toMatch(/female/);
    expect(container.querySelector('.qc-mendel--fail')?.textContent).toMatch(/15\.00%/);
    // Relatedness association matrix renders the observed relationship, flagged as an error.
    expect(screen.getByText('Relatedness vs pedigree')).toBeInTheDocument();
    // Symmetric matrix: the observed relationship shows in both mirror cells.
    expect(screen.getAllByText('unrelated').length).toBeGreaterThanOrEqual(1);
    expect(container.querySelector('.qc-matrix-cell--fail')).toBeTruthy();
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
    expect(container.querySelector('.qc-genotype-sex--match')?.textContent).toMatch(/male/);
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
          cat7_transmitted: 40,
          cat8_absent: 2,
          informative_sites: 42,
          status: 'pass',
          message: 'Paternity supported: paternal transmission observed.',
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

    expect(await screen.findByText('Paternity (cfDNA categories 7/8)')).toBeInTheDocument();
    expect(screen.getByText(/Father FATHER — 40 paternal-transmitted/)).toBeInTheDocument();
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
