// The recessive view's genes: whether the fetus inherited each parent's allele, and the fetal
// status with its probabilities (risk H6: a fetal-risk number must read as what it is).

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import NiptRecessiveGenes from '../NiptRecessiveGenes';
import { recessiveProbabilitiesText, recessiveStatusTone } from '../niptClassification';

describe('NiptRecessiveGenes', () => {
  it('lists each gene with its fetal status, its probabilities and each allele the fetus inherited', () => {
    render(
      <NiptRecessiveGenes
        genes={[
          {
            gene: 'GENEA',
            maternal: [{ variant_id: '7-100-A-G', inherited_probability: 0.97, category: 3, note: null }],
            paternal: [{ variant_id: '7-200-C-T', inherited_probability: 1, category: 7, note: null }],
            risk: 0.97,
            carrier_probability: 0.03,
            unaffected_probability: 0,
            status: 'affected',
            mode: 'autosomal',
            maternal_variant_id: '7-100-A-G',
            paternal_variant_id: '7-200-C-T',
            risk_uses_prior: false,
          },
          {
            gene: 'GENEB',
            maternal: [
              { variant_id: '7-300-A-G', inherited_probability: null, category: 3, note: 'indel: the maternal model is less accurate' },
            ],
            paternal: [{ variant_id: '7-400-C-T', inherited_probability: 1, category: 7, note: null }],
            risk: 0.5,
            carrier_probability: 0.5,
            unaffected_probability: 0,
            status: 'undetermined',
            mode: 'autosomal',
            maternal_variant_id: '7-300-A-G',
            paternal_variant_id: '7-400-C-T',
            risk_uses_prior: true,
          },
          {
            gene: 'GENEX',
            maternal: [{ variant_id: 'X-5000000-A-G', inherited_probability: 0.9, category: 4, note: null }],
            paternal: [],
            risk: 0.9,
            carrier_probability: 0,
            unaffected_probability: 0.1,
            status: 'affected',
            mode: 'x_linked_son',
            maternal_variant_id: 'X-5000000-A-G',
            paternal_variant_id: null,
            risk_uses_prior: false,
          },
        ]}
      />,
    );
    expect(screen.getByText('GENEA')).toBeInTheDocument();
    expect(screen.getAllByText('Affected')).toHaveLength(2);
    expect(screen.getByText('Undetermined')).toBeInTheDocument();
    expect(screen.getByText('affected 97.0%, carrier 3.0%, unaffected 0.0%')).toBeInTheDocument();
    expect(screen.getByText(/inherited 97.0% · cat 3/)).toBeInTheDocument();
    expect(screen.getByText(/inherited not told · cat 3/)).toBeInTheDocument();
    expect(screen.getByText('indel: the maternal model is less accurate')).toBeInTheDocument();
    expect(screen.getByText(/read at its 50% prior/)).toBeInTheDocument();
    // A son's X-linked gene: his mother's allele alone; no carrier state, no paternal allele.
    expect(screen.getByText('X-linked')).toBeInTheDocument();
    expect(screen.getByText('affected 90.0%, unaffected 10.0%')).toBeInTheDocument();
    expect(screen.getByText("A son does not inherit his father's X.")).toBeInTheDocument();
    expect(screen.getByText(/between 20% and 80%/)).toBeInTheDocument();
    expect(screen.getByText(/A carrier couple's prior is 25%/)).toBeInTheDocument();
  });

  it('says when no gene has both parents carrying', () => {
    render(<NiptRecessiveGenes genes={[]} />);
    expect(screen.getByText(/No gene where both parents carry an allele/)).toBeInTheDocument();
  });

  it('tones a status: affected critical, undetermined a warning, carrier and unaffected a pass', () => {
    expect(recessiveStatusTone('affected')).toContain('critical');
    expect(recessiveStatusTone('undetermined')).toContain('warning');
    expect(recessiveStatusTone('carrier')).toContain('success');
    expect(recessiveStatusTone('unaffected')).toContain('success');
    expect(recessiveStatusTone(null)).toContain('neutral');
  });

  it('words the probabilities, a son X-linked gene without a carrier state', () => {
    expect(recessiveProbabilitiesText(0.25, 0.5, 0.25, false)).toBe('affected 25.0%, carrier 50.0%, unaffected 25.0%');
    expect(recessiveProbabilitiesText(0.5, 0, 0.5, true)).toBe('affected 50.0%, unaffected 50.0%');
    expect(recessiveProbabilitiesText(null, null, null, false)).toBe('affected —, carrier —, unaffected —');
  });
});
