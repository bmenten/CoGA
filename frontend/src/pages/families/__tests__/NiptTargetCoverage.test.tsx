// The plasma's per-target coverage QC on the NIPT page (REQ-NIPT-006): how many genes have a
// weak target, where a fetal variant can be missed, and the way to the coverage page that
// names them.

import { render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { MemoryRouter } from 'react-router';
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

const renderCoverage = (props: Partial<ComponentProps<typeof NiptTargetCoverage>> = {}) =>
  render(
    <MemoryRouter>
      <NiptTargetCoverage
        coverage={coverage}
        scoped
        detailsHref="/families/NIPT001/nipt/coverage?panel_id=panel-1"
        {...props}
      />
    </MemoryRouter>,
  );

describe('NiptTargetCoverage', () => {
  it('counts the genes where a fetal variant can be missed and links to the coverage page', () => {
    renderCoverage();
    expect(screen.getByText('950x')).toBeInTheDocument();
    expect(screen.getByText(/of the selected genes/)).toBeInTheDocument();
    // GENEA's weak target and GENEZ, selected but not captured.
    expect(screen.getByRole('status')).toHaveTextContent(
      '2 genes with a weak target (mean below 300x or a base without coverage): a fetal variant there can be missed. 1 of them is not a target of the panel.',
    );
    expect(screen.getByRole('link', { name: 'Coverage details' })).toHaveAttribute(
      'href',
      '/families/NIPT001/nipt/coverage?panel_id=panel-1',
    );
    // The genes are named on the coverage page, not listed here.
    expect(screen.queryByText(/GENEA/)).not.toBeInTheDocument();
  });

  it('says when every target in scope is covered', () => {
    renderCoverage({ coverage: { ...coverage, genes: [coverage.genes[1]] }, scoped: false });
    expect(screen.getByText(/Every target reaches 300x/)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Coverage details' })).toBeInTheDocument();
  });
});
