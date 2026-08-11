import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import SmallVariantPairCards from '../SmallVariantPairCards';
import type { SmallVariant, SmallVariantGroup } from '../smallVariantSearch';

const variant = (id: string, gt: string, start: number): SmallVariant =>
  ({
    _id: id,
    chr: '3',
    start,
    end: start,
    type: 'SNV',
    gene: 'TRNT1',
    ref: 'A',
    alt: 'G',
    impact: 'MODERATE',
    effect: 'missense_variant',
    genotypes: [{ sample: 'PROBAND', gt }],
  }) as unknown as SmallVariant;

const renderPair = (group: Partial<SmallVariantGroup>) =>
  render(
    <MemoryRouter>
      <SmallVariantPairCards
        groups={[
          {
            group_type: 'compound_het',
            group_key: 'pair-1',
            gene: 'TRNT1',
            variants: [variant('v1', '0|1', 3129108), variant('v2', '1|0', 3139593)],
            ...group,
          } as SmallVariantGroup,
        ]}
        members={[{ sample_id: 'PROBAND', role: 'proband', affected: true } as never]}
        familyId="F1"
        projectId="P1"
        locationSearch=""
        tags={[]}
        onEditReview={vi.fn()}
      />
    </MemoryRouter>,
  );

const phasingRow = () =>
  screen.getByText('Phasing').closest('.variant-compound-het-summary-row') as HTMLElement;

describe('SmallVariantPairCards', () => {
  it('marks a read-backed trans pair', () => {
    renderPair({ phase: 'trans' });
    expect(within(phasingRow()).getByText('In trans · read-backed')).toBeInTheDocument();
  });

  it('says so plainly when the reads did not resolve the pair', () => {
    renderPair({ phase: 'unknown' });
    expect(within(phasingRow()).getByText('Not resolved by reads')).toBeInTheDocument();
  });

  it('treats a payload without a phase as unresolved rather than claiming trans', () => {
    renderPair({});
    expect(within(phasingRow()).getByText('Not resolved by reads')).toBeInTheDocument();
  });

  it('keeps the curator phase separate from what the reads say', () => {
    renderPair({ phase: 'trans', review: { phase_status: 'likely_cis' } as never });
    // The reviewer's own call stands on its own row and is not overwritten.
    const curated = screen.getByText('Phase').closest('.variant-compound-het-summary-row') as HTMLElement;
    expect(within(curated).getByText('Likely cis')).toBeInTheDocument();
    expect(within(phasingRow()).getByText('In trans · read-backed')).toBeInTheDocument();
  });
});
