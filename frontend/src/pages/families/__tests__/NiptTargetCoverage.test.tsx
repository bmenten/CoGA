// The plasma's per-target coverage QC (REQ-NIPT-006): where a fetal variant can be missed.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import NiptTargetCoverage from '../NiptTargetCoverage';

const target = (gene: string, mean: number) => ({
  chr: '7',
  start: 100,
  end: 200,
  gene,
  attribute: `${gene};NM_1.1;ENST1;ENSE1;3`,
  mean,
  median: mean,
  min: mean,
  proportion_covered: 100,
});

const coverage = {
  targets: 12,
  median_mean: 950,
  q05_mean: 280,
  below_critical: 1,
  below_advisory: 6,
  zero_mean: 0,
  incomplete: 0,
  critical_mean_depth: 300,
  advisory_mean_depth: 1000,
  genes: [
    { gene: 'GENEA', targets: 10, weak_targets: 1, min_mean: 250, mean_of_means: 900, weak: [target('GENEA', 250)] },
    { gene: 'GENEB', targets: 2, weak_targets: 0, min_mean: 1200, mean_of_means: 1250, weak: [] },
    { gene: 'GENEZ', targets: 0, weak_targets: 0, min_mean: null, mean_of_means: null, weak: [] },
  ],
};

describe('NiptTargetCoverage', () => {
  it('names the genes where a fetal variant can be missed', () => {
    render(<NiptTargetCoverage coverage={coverage} scoped />);
    expect(screen.getByText('950x')).toBeInTheDocument();
    expect(screen.getByText(/of the selected genes/)).toBeInTheDocument();
    expect(screen.getByText(/2 genes with a weak target/)).toBeInTheDocument();
    expect(screen.getByText('GENEA · 1 of 10 targets weak')).toBeInTheDocument();
    expect(screen.getByTitle('GENEA exon 3: mean 250x')).toBeInTheDocument();
    expect(screen.getByText('GENEZ · not a target')).toBeInTheDocument();
    expect(screen.queryByText(/GENEB ·/)).not.toBeInTheDocument();
  });

  it('says when every target in scope is covered', () => {
    render(<NiptTargetCoverage coverage={{ ...coverage, genes: [coverage.genes[1]] }} scoped={false} />);
    expect(screen.getByText(/Every target reaches 300x/)).toBeInTheDocument();
  });
});
