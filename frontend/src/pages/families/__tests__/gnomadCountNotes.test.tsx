import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import SmallVariantResults from '../SmallVariantResults';
import { GNOMAD_COUNT_NOTE_DETAIL, gnomadCountNote } from '../gnomadCountNotes';
import type { SmallVariant, SmallVariantPage } from '../smallVariantSearch';
import { smallVariant } from '../../../test/variants';

// A maximum gnomAD count filter keeps a variant whose annotation holds no such count, as absent
// from gnomAD. The list says in one line how many of the variants it shows may be in gnomAD (a
// gnomAD frequency, or a callset not annotated with gnomAD) and passed each filter unchecked.
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

const annotated: Partial<SmallVariant> = { gnomad_annotated: ['exomes', 'genomes'] };
const counted = variant('1-1000-C-T', {
  ...annotated,
  gnomad_af: 0.001,
  gnomad_hom_count: 2,
  annotation_extra: { gnomad_ac: 40 },
});
// In gnomAD (a frequency), without its counts.
const uncounted = variant('1-2000-G-A', { ...annotated, gnomad_af: 0.0001 });
// In a callset annotated with gnomAD, without a frequency or a count: absent from gnomAD.
const absent = variant('1-3000-A-G', annotated);
// From a callset not annotated with gnomAD: nothing tells that it is absent.
const unannotated = variant('1-4000-T-C');

const line = (filters: 'filter' | 'filters', counts: string) =>
  `Passed the gnomAD count ${filters} unchecked (no count, possibly in gnomAD): ${counts}.`;

describe('gnomadCountNote', () => {
  it('names in one line each applied count filter that kept shown variants unchecked', () => {
    expect(
      gnomadCountNote(
        'max_gnomad_hom_count=10&max_gnomad_hemi_count=10&max_gnomad_ac=5',
        page([counted, uncounted, unannotated]),
      ),
    ).toBe(line('filters', 'AC 2, H/H 2 of 3 variants shown'));
  });

  it('leaves out a variant gnomAD does not list, which the filter keeps as absent', () => {
    expect(gnomadCountNote('max_gnomad_hom_count=10&max_gnomad_ac=5', page([counted, absent]))).toBeNull();
    expect(gnomadCountNote('max_gnomad_hom_count=10', page([uncounted, absent]))).toBe(
      line('filter', 'H/H 1 of 2 variants shown'),
    );
  });

  it("reads any of gnomAD's nuclear frequencies as in gnomAD, and not gnomAD-MT's", () => {
    const exomesOnly = variant('1-5000-C-G', { ...annotated, population_frequencies: { gnomad_exomes_popmax_af: 0.01 } });
    const mitochondrial = variant('MT-100-A-G', {
      ...annotated,
      chr: 'MT',
      start: 100,
      end: 100,
      population_frequencies: { gnomad_mt_af_hom: 0.01 },
    });
    expect(gnomadCountNote('max_gnomad_hom_count=10', page([exomesOnly, mitochondrial]))).toBe(
      line('filter', 'H/H 1 of 2 variants shown'),
    );
  });

  it('asks a hemizygote count only on chrX and chrY outside the pseudo-autosomal regions', () => {
    const onX = { ...annotated, chr: 'X', gnomad_af: 0.01, gnomad_hom_count: 1, annotation_extra: { gnomad_ac: 9 } };
    const inPar = variant('X-20000-C-T', { ...onX, start: 20000, end: 20000, hemizygous_in_males: false });
    const hemizygous = variant('X-3000000-C-G', { ...onX, start: 3000000, end: 3000000, hemizygous_in_males: true });
    const hemizygousCounted = variant('X-3000001-C-A', {
      ...onX,
      start: 3000001,
      end: 3000001,
      hemizygous_in_males: true,
      annotation_extra: { gnomad_ac: 9, gnomad_hemi_count: 3 },
    });
    expect(
      gnomadCountNote('max_gnomad_hemi_count=1', page([counted, uncounted, inPar, hemizygous, hemizygousCounted])),
    ).toBe(line('filter', 'hemi 1 of 5 variants shown'));
    expect(gnomadCountNote('max_gnomad_hemi_count=1', page([counted, uncounted, inPar, hemizygousCounted]))).toBeNull();
  });

  it('says so for one variant shown', () => {
    expect(gnomadCountNote('max_gnomad_hom_count=10', page([uncounted]))).toBe(
      line('filter', 'H/H 1 of 1 variant shown'),
    );
  });

  it('counts each variant of a compound-het gene once, with the single variants', () => {
    const note = gnomadCountNote(
      'max_gnomad_hom_count=10',
      page([uncounted], {
        variant_groups: [
          {
            group_type: 'compound_het',
            group_key: 'GENE1',
            gene: 'GENE1',
            gene_id: null,
            variants: [uncounted, counted],
            pairs: [],
            inherited_from: {},
          },
        ],
      }),
    );
    expect(note).toBe(line('filter', 'H/H 1 of 2 variants shown'));
  });

  it('says nothing without a count filter, when every variant has the count, or without results', () => {
    expect(gnomadCountNote('max_gnomad_af=0.01', page([uncounted]))).toBeNull();
    expect(gnomadCountNote('max_gnomad_hom_count=', page([uncounted]))).toBeNull();
    expect(gnomadCountNote('max_gnomad_hom_count=10&max_gnomad_ac=5', page([counted]))).toBeNull();
    expect(gnomadCountNote('max_gnomad_hom_count=10', page([]))).toBeNull();
    expect(gnomadCountNote('max_gnomad_hom_count=10', undefined)).toBeNull();
  });
});

describe('SmallVariantResults', () => {
  it('shows the line above the variants the count filter kept, what it counts on hover', () => {
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
    expect(note).toHaveTextContent(line('filter', 'H/H 1 of 2 variants shown'));
    expect(note).toHaveAttribute('title', GNOMAD_COUNT_NOTE_DETAIL);
    expect(note).toHaveClass('variant-workspace-feedback', 'variant-workspace-feedback--warning');
  });
});
