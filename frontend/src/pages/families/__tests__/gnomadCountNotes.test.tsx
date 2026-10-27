import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import SmallVariantResults from '../SmallVariantResults';
import { gnomadCountNotes } from '../gnomadCountNotes';
import type { SmallVariant, SmallVariantPage } from '../smallVariantSearch';
import { smallVariant } from '../../../test/variants';

// CLIN-67: a maximum gnomAD count filter keeps a variant whose annotation holds no such count,
// as absent from gnomAD. The laboratory's VCFs carry gnomAD's frequencies but no counts, so the
// filters kept every variant without a word; the list now says how many it kept that way.
const variant = (id: string, fields: Partial<SmallVariant> = {}): SmallVariant =>
  smallVariant({
    _id: id,
    chr: '1',
    start: 1000,
    end: 1000,
    type: 'SNV',
    genotypes: [],
    ...fields,
  });

const page = (variants: SmallVariant[], fields: Partial<SmallVariantPage> = {}): SmallVariantPage => ({
  variants,
  total: variants.length,
  total_is_estimated: false,
  count_limit: null,
  ...fields,
});

const counted = variant('1-1000-C-T', {
  gnomad_hom_count: 2,
  annotation_extra: { gnomad_ac: 40, gnomad_hemi_count: 0 },
});
const uncounted = variant('1-2000-G-A', { gnomad_af: 0.0001 });

describe('gnomadCountNotes', () => {
  it('names each applied count filter the shown variants have no count for', () => {
    expect(
      gnomadCountNotes(
        'max_gnomad_hom_count=10&max_gnomad_hemi_count=10&max_gnomad_ac=5',
        page([counted, uncounted, variant('1-3000-A-G')]),
      ),
    ).toEqual([
      '2 of the 3 variants shown have no gnomAD allele count: the Max gnomAD AC filter keeps them, as absent from gnomAD.',
      '2 of the 3 variants shown have no gnomAD homozygote count: the Max gnomAD H/H filter keeps them, as absent from gnomAD.',
      '2 of the 3 variants shown have no gnomAD hemizygote count: the Max gnomAD hemi filter keeps them, as absent from gnomAD.',
    ]);
  });

  it('says so when none of them has the count, one does not, or one variant is shown', () => {
    expect(gnomadCountNotes('max_gnomad_hom_count=10', page([uncounted, variant('1-3000-A-G')]))).toEqual([
      'None of the 2 variants shown has a gnomAD homozygote count: the Max gnomAD H/H filter keeps them, as absent from gnomAD.',
    ]);
    expect(gnomadCountNotes('max_gnomad_hom_count=10', page([counted, uncounted]))).toEqual([
      '1 of the 2 variants shown has no gnomAD homozygote count: the Max gnomAD H/H filter keeps it, as absent from gnomAD.',
    ]);
    expect(gnomadCountNotes('max_gnomad_hom_count=10', page([uncounted]))).toEqual([
      'The variant shown has no gnomAD homozygote count: the Max gnomAD H/H filter keeps it, as absent from gnomAD.',
    ]);
  });

  it('counts a compound-het pair member once, with the single variants', () => {
    const notes = gnomadCountNotes(
      'max_gnomad_hom_count=10',
      page([uncounted], {
        variant_groups: [
          {
            group_type: 'compound_het',
            group_key: 'GENE1',
            gene: 'GENE1',
            gene_id: null,
            variants: [uncounted, counted],
            review: null,
            phase: 'unknown',
            phase_evidence: null,
          },
        ],
      }),
    );
    expect(notes).toEqual([
      '1 of the 2 variants shown has no gnomAD homozygote count: the Max gnomAD H/H filter keeps it, as absent from gnomAD.',
    ]);
  });

  it('says nothing without a count filter, when every variant has the count, or without results', () => {
    expect(gnomadCountNotes('max_gnomad_af=0.01', page([uncounted]))).toEqual([]);
    expect(gnomadCountNotes('max_gnomad_hom_count=', page([uncounted]))).toEqual([]);
    expect(gnomadCountNotes('max_gnomad_hom_count=10&max_gnomad_ac=5', page([counted]))).toEqual([]);
    expect(gnomadCountNotes('max_gnomad_hom_count=10', page([]))).toEqual([]);
    expect(gnomadCountNotes('max_gnomad_hom_count=10', undefined)).toEqual([]);
  });
});

describe('SmallVariantResults', () => {
  it('shows the note above the variants the count filter kept', () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter>
          <SmallVariantResults
            data={page([counted, uncounted])}
            familyId="FAM1"
            locationSearch=""
            members={[]}
            onPageChange={() => {}}
            page={1}
            requestQueryString="max_gnomad_hom_count=10"
            totalPages={1}
            tags={[]}
            onToggleReviewTag={async () => {}}
            onSaveReview={async () => {}}
          />
        </MemoryRouter>
      </QueryClientProvider>,
    );

    const note = screen.getByRole('note');
    expect(note).toHaveTextContent(
      '1 of the 2 variants shown has no gnomAD homozygote count: the Max gnomAD H/H filter keeps it, as absent from gnomAD.',
    );
    expect(note).toHaveClass('variant-workspace-feedback', 'variant-workspace-feedback--warning');
  });
});
