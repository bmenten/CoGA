// The recessive view's genes: whether the fetus inherited each parent's allele and the
// fetal risk (risk H6: a fetal-risk number must read as what it is).

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import NiptRecessiveGenes, { recessiveRiskTone } from '../NiptRecessiveGenes';

describe('NiptRecessiveGenes', () => {
  it('lists each gene with its risk and the probability that each allele was inherited', () => {
    render(
      <NiptRecessiveGenes
        genes={[
          {
            gene: 'GENEA',
            maternal: [{ variant_id: '7-100-A-G', inherited_probability: 0.97, category: 3, note: null }],
            paternal: [{ variant_id: '7-200-C-T', inherited_probability: 1, category: 7, note: null }],
            risk: 0.97,
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
            maternal_variant_id: '7-300-A-G',
            paternal_variant_id: '7-400-C-T',
            risk_uses_prior: true,
          },
        ]}
      />,
    );
    expect(screen.getByText('GENEA')).toBeInTheDocument();
    expect(screen.getAllByText('97.0%').length).toBeGreaterThan(0);
    expect(screen.getByText(/inherited 97.0% · cat 3/)).toBeInTheDocument();
    expect(screen.getByText(/inherited not told · cat 3/)).toBeInTheDocument();
    expect(screen.getByText('indel: the maternal model is less accurate')).toBeInTheDocument();
    expect(screen.getByText(/read at its 50% prior/)).toBeInTheDocument();
    expect(screen.getByText(/A carrier couple's prior is 25%/)).toBeInTheDocument();
  });

  it('says when no gene has both parents carrying', () => {
    render(<NiptRecessiveGenes genes={[]} />);
    expect(screen.getByText(/No gene where both parents carry an allele/)).toBeInTheDocument();
  });

  it('tones a risk high, low or in between', () => {
    expect(recessiveRiskTone(0.97)).toContain('critical');
    expect(recessiveRiskTone(0.01)).toContain('success');
    expect(recessiveRiskTone(0.25)).toContain('warning');
    expect(recessiveRiskTone(null)).toContain('neutral');
  });
});
