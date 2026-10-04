// NIPT per-variant classification display — REQ-UI-001 (risk H6).
// The analyst reads the fetal category, confidence and observed/expected VAF off
// this block, so the numbers must render faithfully (confidence 2 dp, VAF as %,
// em dash for missing) and QC flags must be visible.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import NiptClassificationBlock from '../NiptClassificationBlock';
import type { NiptClassification } from '../smallVariantSearch';

const base: NiptClassification = {
  category: 7,
  category_label: 'Paternal transmitted',
  maternal_state: 'hom_ref',
  fetal_inheritance: 'paternal',
  expected_vaf: 0.05,
  observed_vaf: 0.048,
  confidence: 0.873,
  flags: [],
};

describe('NiptClassificationBlock', () => {
  it('shows the category, label and confidence to two decimals', () => {
    render(<NiptClassificationBlock nipt={base} />);
    expect(screen.getByText('Category 7')).toBeInTheDocument();
    expect(screen.getByText('Paternal transmitted')).toBeInTheDocument();
    expect(screen.getByText('confidence 0.87')).toBeInTheDocument();
  });

  it('formats observed/expected VAF as percentages', () => {
    render(<NiptClassificationBlock nipt={{ ...base, observed_vaf: 0.123, expected_vaf: 0.05 }} />);
    expect(screen.getByText('12.3% / 5.0%')).toBeInTheDocument();
  });

  it('renders an em dash for a missing observed VAF', () => {
    render(<NiptClassificationBlock nipt={{ ...base, observed_vaf: null }} />);
    expect(screen.getByText('— / 5.0%')).toBeInTheDocument();
  });

  it('shows "Unclassified" when no category and renders QC flags', () => {
    render(
      <NiptClassificationBlock
        nipt={{ ...base, category: null, flags: ['low_depth', 'dropout'] }}
      />,
    );
    expect(screen.getByText('Unclassified')).toBeInTheDocument();
    expect(screen.getByText('low_depth')).toBeInTheDocument();
    expect(screen.getByText('dropout')).toBeInTheDocument();
  });
});

describe('NiptClassificationBlock fetal-inheritance evidence', () => {
  it('shows the plasma reads, the father and the inheritance probabilities', () => {
    render(
      <NiptClassificationBlock
        nipt={{
          ...base,
          cf_alt_reads: 150,
          cf_depth: 1500,
          father_state: 'het',
          father_vaf: 0.5,
          father_depth: 300,
          paternal_transmission_probability: 0.9999,
          maternal_allele_probability: 0.86,
          fetal_hom_alt_probability: 0.02,
        }}
      />,
    );
    expect(screen.getByText('150 of 1500')).toBeInTheDocument();
    expect(screen.getByText('het · 50.0% · 300×')).toBeInTheDocument();
    expect(screen.getByText('Paternal allele inherited')).toBeInTheDocument();
    expect(screen.getByText('100.0%')).toBeInTheDocument();
    expect(screen.getByText('86.0%')).toBeInTheDocument();
    expect(screen.getByText('Fetus homozygous')).toBeInTheDocument();
  });

  it('says when the plasma has no call and its depth comes from the target coverage', () => {
    render(
      <NiptClassificationBlock
        nipt={{ ...base, cf_alt_reads: 0, cf_depth: 1420, cf_depth_estimated: true, father_state: 'absent' }}
      />,
    );
    expect(screen.getByText('no call at ~1420× (target coverage)')).toBeInTheDocument();
    expect(screen.getByText('no call (reference)')).toBeInTheDocument();
  });

  it("shows a de novo candidate's triage", () => {
    render(
      <NiptClassificationBlock
        nipt={{
          ...base,
          category: 1,
          de_novo: { window: 'strict', score: 19, label: 'high', reasons: ['impact MODERATE', 'novel'], other_cfdna_carriers: 0 },
        }}
      />,
    );
    expect(screen.getByText('De novo: high priority')).toBeInTheDocument();
    expect(screen.getByText(/score 19 · strict window · impact MODERATE, novel/)).toBeInTheDocument();
  });
});
