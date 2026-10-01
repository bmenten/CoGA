import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CandidateCapNotice, { candidateCapMessage } from '../CandidateCapNotice';

// #725 follow-up: a family search that read only part of the callset says so, where it
// stopped and how to narrow it, instead of only an estimated total.
describe('CandidateCapNotice', () => {
  it('says where a capped candidate read stopped, that results may be incomplete, and how to narrow', () => {
    render(<CandidateCapNotice page={{ candidates_capped: true, candidate_limit: 5000 }} noun="variants" />);
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent(
      'Results may be incomplete: the search stopped after the first 5,000 candidate variants, so matches ' +
        'beyond that point are not shown and the total is a lower bound. Narrow the filters (a region, a ' +
        'gene panel or a gene) so the search reads the whole callset.',
    );
    // The shared warning style of the variant workspaces, not a new one.
    expect(notice).toHaveClass('variant-workspace-feedback', 'variant-workspace-feedback--warning');
  });

  it('says a truncated ranking covered only its window', () => {
    render(<CandidateCapNotice page={{ ranking_truncated: true, candidate_limit: 5000 }} noun="SVs" />);
    expect(screen.getByRole('status')).toHaveTextContent(
      'Ranking may be incomplete: the prioritizer ranked only the first 5,000 candidate SVs, so the top ' +
        'candidate may not be shown. Narrow the filters (a region, a gene panel or a gene, or tighter ' +
        'frequency or impact) to rank the full set.',
    );
  });

  it('prefers the capped read when both flags are set: matches may be missing anywhere', () => {
    expect(
      candidateCapMessage({ candidates_capped: true, ranking_truncated: true, candidate_limit: 1250 }, 'variants'),
    ).toMatch(/^Results may be incomplete: the search stopped after the first 1,250 candidate variants/);
  });

  it('still warns when an older backend sends no limit', () => {
    expect(candidateCapMessage({ candidates_capped: true }, 'SVs')).toMatch(
      /the search stopped after a limited number of candidate SVs/,
    );
    expect(candidateCapMessage({ ranking_truncated: true, candidate_limit: null }, 'variants')).toMatch(
      /ranked only a limited number of candidate variants/,
    );
  });

  it('renders nothing for a complete search or no page yet', () => {
    const { container, rerender } = render(<CandidateCapNotice page={undefined} noun="variants" />);
    expect(container).toBeEmptyDOMElement();
    rerender(<CandidateCapNotice page={{ candidates_capped: false, ranking_truncated: false }} noun="variants" />);
    expect(container).toBeEmptyDOMElement();
  });
});
