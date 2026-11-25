import { render, screen, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import NiptDiagnosticCandidatesSection, { describeDiagnosticCriteria } from '../NiptDiagnosticCandidates';
import type { NiptDiagnosticCandidateOut, NiptDiagnosticCandidatesOut } from '../../../lib/apiSchema.generated';

const CRITERIA: NiptDiagnosticCandidatesOut['criteria'] = {
  reference: 'R NIPT-M v0.7.3 diagnostic criteria (blinded analysis, step 15)',
  rare_max_af_dominant: 0.001,
  rare_max_af_recessive: 0.01,
  effect_impacts: ['HIGH', 'MODERATE'],
  min_spliceai: 0.2,
  clinvar_max_af: 0.05,
  cohort_common_min_samples: 3,
  repeat_min_units: 3,
};

const candidate = (id: string, gene: string, extra: Partial<NiptDiagnosticCandidateOut> = {}) =>
  ({
    variant: { _id: id, gene },
    confidence: 'standard',
    flags: [],
    ...extra,
  }) as unknown as NiptDiagnosticCandidateOut;

const DATA = {
  family_id: 'NIPTFAM1',
  trio: { cfdna_sample_id: 'CFDNA1', father_sample_id: 'FATHER1', fetus_sample_ids: [] },
  fetal_fraction: {},
  criteria: CRITERIA,
  panel_id: 'panel-1',
  gene: null,
  capped: false,
  de_novo: [candidate('7-500-G-GCAGCAG', 'GENEC', { confidence: 'low', flags: ['repeat_context'] })],
  paternal: [candidate('7-200-C-T', 'GENEA', { flags: ['clinvar_pathogenic'] })],
  maternal: [],
  recessive_genes: [
    {
      gene: 'GENEA',
      risk: 0.24,
      carrier_probability: 0.52,
      unaffected_probability: 0.24,
      status: 'undetermined',
      mode: 'autosomal',
      risk_uses_prior: false,
      maternal: [],
      paternal: [],
      maternal_variant_id: '7-100-A-G',
      paternal_variant_id: '7-200-C-T',
    },
    {
      gene: 'GENEX',
      risk: 0.95,
      carrier_probability: 0,
      unaffected_probability: 0.05,
      status: 'affected',
      mode: 'x_linked_son',
      risk_uses_prior: false,
      maternal: [],
      paternal: [],
      maternal_variant_id: 'X-5000000-A-G',
      paternal_variant_id: null,
    },
  ],
  recessive_variants: [candidate('7-100-A-G', 'GENEA'), candidate('7-200-C-T', 'GENEA')],
} as unknown as NiptDiagnosticCandidatesOut;

const renderSection = (data: NiptDiagnosticCandidatesOut = DATA) =>
  render(
    <NiptDiagnosticCandidatesSection
      data={data}
      scope="the analysis's diagnostic panel, NIPT diagnostic (version 3), as the sign-out freezes it."
      renderVariant={(item) => <p data-testid="variant">{`${item.variant.gene} ${item.variant._id}`}</p>}
    />,
  );

describe('NiptDiagnosticCandidatesSection', () => {
  it('names the locked criteria with their version', () => {
    renderSection();
    expect(screen.getByText(/R NIPT-M v0\.7\.3 diagnostic criteria/)).toBeInTheDocument();
    expect(describeDiagnosticCriteria(CRITERIA)).toContain('rare at 0.1% or less (recessive alleles 1.0%');
  });

  it('lists each view with its count, a low-confidence candidate and its flags', () => {
    renderSection();
    const deNovo = screen.getByRole('heading', { name: 'De novo in the fetus (1)' }).parentElement as HTMLElement;
    expect(within(deNovo).getByText('Low confidence.')).toBeInTheDocument();
    expect(within(deNovo).getByText(/indel in a repeat/)).toBeInTheDocument();
    expect(within(deNovo).getByTestId('variant')).toHaveTextContent('GENEC 7-500-G-GCAGCAG');
    const paternal = screen.getByRole('heading', { name: 'Paternal, inherited by the fetus (1)' })
      .parentElement as HTMLElement;
    expect(within(paternal).getByText('ClinVar pathogenic')).toBeInTheDocument();
    const maternal = screen.getByRole('heading', { name: 'Maternal (0)' })
      .parentElement as HTMLElement;
    expect(within(maternal).getByText('None.')).toBeInTheDocument();
    // Each recessive gene with its status, its probabilities and the pair they rest on, a son's
    // X-linked gene on his mother's allele alone; then the alleles of these genes.
    expect(
      screen.getByText(
        'GENEA: Undetermined (affected 24.0%, carrier 52.0%, unaffected 24.0%; maternal 7-100-A-G, paternal 7-200-C-T)',
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText(
        "GENEX (X-linked, a son: his mother’s allele alone): Affected (affected 95.0%, unaffected 5.0%; maternal X-5000000-A-G)",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText('The qualifying alleles of these genes:')).toBeInTheDocument();
    expect(screen.getAllByTestId('variant').map((node) => node.textContent)).toEqual([
      'GENEC 7-500-G-GCAGCAG',
      'GENEA 7-200-C-T',
      'GENEA 7-100-A-G',
      'GENEA 7-200-C-T',
    ]);
  });

  it("names its scope, and a son's paternal-X allele by what it is", () => {
    renderSection({
      ...DATA,
      de_novo: [{ ...DATA.de_novo[0], flags: ['paternal_x_allele_in_male_fetus'] }],
    });
    expect(screen.getByText(/the analysis's diagnostic panel, NIPT diagnostic \(version 3\)/)).toBeInTheDocument();
    expect(screen.getByText(/a son's X allele his father also calls/)).toBeInTheDocument();
  });

  it('says when the lists may be incomplete', () => {
    renderSection({ ...DATA, capped: true });
    expect(screen.getByText(/These lists may be incomplete/)).toBeInTheDocument();
  });
});
