// The NIPT quality checks a reader confirms before reading any variant (REQ-UI-001,
// risks H4/H6): the fetal fraction, the fetal sex from two signals, paternity, the plasma
// sample and the target coverage.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import type { NiptQcOut } from '../../../lib/apiSchema.generated';
import NiptQcPanel from '../NiptQcPanel';

const qc: NiptQcOut = {
  de_novo_window: { strict_min: 0.08, strict_max: 0.14, loose_min: 0.06, loose_max: 0.175 },
  paternity: {
    hom_alt_transmitted: 480,
    hom_alt_not_transmitted: 20,
    het_transmitted: 1000,
    het_not_transmitted: 1000,
    hom_alt_rate: 0.96,
    het_rate: 0.5,
    status: 'pass',
    message: 'Paternity supported.',
  },
  fetal_sex: {
    call: 'female',
    paternal_x: 'female',
    x_transmitted: 40,
    x_not_transmitted: 0,
    informative_sites: 40,
    chry_profile: 'female_no_chrY_signal',
    y_ratio: 0,
    x_ratio: 1.25,
    chry_fetal_fraction: null,
  },
  plasma_profile_status: 'pass',
  plasma_profile_message: 'Female plasma; no chrY signal.',
  target_coverage: {
    targets: 60000,
    median_mean: 800,
    q05_mean: 350,
    below_critical: 1500,
    below_advisory: 40000,
    zero_mean: 500,
    incomplete: 1000,
    critical_mean_depth: 300,
    advisory_mean_depth: 1000,
    genes: [],
  },
  quality_failures: { low_quality: 1200, few_alt_reads: 900 },
  model: {
    reference: 'R NIPT-M v0.5.1 validation',
    overdispersion: 0.0037,
    maternal_het_bias: -0.0112,
    min_quality: 20,
    min_alt_reads: 5,
    min_vaf: 0.01,
    vaf_ff_fraction: 0.25,
    max_strand_bias_fs: 20,
    father_het_min_vaf: 0.2,
    father_hom_alt_min_vaf: 0.8,
    min_father_depth: 20,
  },
};

const ff = {
  ff: 0.18,
  ff_computed: 0.18,
  ff_median: 0.179,
  ci_low: 0.178,
  ci_high: 0.182,
  n_sites: 2000,
  method: 'category7_pooled',
  low_confidence: false,
};

describe('NiptQcPanel', () => {
  it('shows each check with its verdict', () => {
    render(<NiptQcPanel qc={qc} fetalFraction={ff} />);
    expect(screen.getByText('Fetal fraction 18.0% (95% CI 17.8%–18.2%)')).toBeInTheDocument();
    expect(screen.getByText(/Fetal window for de novo alleles 8.0%–14.0%/)).toBeInTheDocument();
    expect(screen.getByText('Fetal sex: Female')).toBeInTheDocument();
    expect(screen.getByText(/40 of the father's X alleles seen/)).toBeInTheDocument();
    expect(screen.getByText(/Homozygous paternal alleles seen 480 of 500 \(96.0%; all expected\)/)).toBeInTheDocument();
    expect(screen.getByText(/Het paternal alleles seen 1000 of 2000 \(50.0%; half expected\)/)).toBeInTheDocument();
    expect(screen.getByText('Target coverage: median 800x over 60,000 targets')).toBeInTheDocument();
    expect(screen.getByText(/1,200 quality \(TLOD\) below the minimum; 900 too few alt reads/)).toBeInTheDocument();
    expect(screen.getAllByText('Pass').length).toBe(4);
  });

  it('fails a discordant fetal sex and a failed paternity', () => {
    render(
      <NiptQcPanel
        qc={{
          ...qc,
          fetal_sex: { ...qc.fetal_sex, call: 'discordant' },
          paternity: { ...qc.paternity, status: 'fail', message: 'Another father.' },
        }}
        fetalFraction={ff}
      />,
    );
    expect(screen.getByText('Fetal sex: Discordant')).toBeInTheDocument();
    expect(screen.getByText(/The two signals disagree/)).toBeInTheDocument();
    expect(screen.getAllByText('Fail').length).toBe(2);
  });

  it('warns on a low-confidence fetal fraction', () => {
    render(<NiptQcPanel qc={qc} fetalFraction={{ ...ff, low_confidence: true }} />);
    expect(screen.getByText(/Low confidence: too few sites or a wide interval/)).toBeInTheDocument();
    expect(screen.getByText('Warning')).toBeInTheDocument();
  });
});
