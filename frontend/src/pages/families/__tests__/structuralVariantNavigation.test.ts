import { describe, expect, it } from 'vitest';
import { SV_FLANK_BP, buildStructuralVariantNavigation } from '../structuralVariantNavigation';
import type { StructuralVariant } from '../structuralVariantSearch';

const variant = (overrides: Partial<StructuralVariant>): StructuralVariant => ({
  _id: 'sv1',
  chr: 'chr5',
  start: 1_000_000,
  end: 1_400_000,
  length: 400_000,
  type: 'DEL',
  genotypes: [],
  ...overrides,
});

const windowOf = (viewHref: string) => {
  const params = new URLSearchParams(viewHref.split('?')[1]);
  return { start: Number(params.get('start')), end: Number(params.get('end')) };
};

describe('buildStructuralVariantNavigation', () => {
  it('spans the whole call plus flanking context rather than the bare breakpoints', () => {
    const { locus, viewHref } = buildStructuralVariantNavigation({
      familyId: 'F1',
      linkSearch: '',
      variant: variant({}),
    });

    // Both junctions on screen with context, not a view parked on the start breakpoint.
    expect(locus).toBe(`chr5:${1_000_000 - SV_FLANK_BP}-${1_400_000 + SV_FLANK_BP}`);
    expect(windowOf(viewHref)).toEqual({ start: 950_000, end: 1_450_000 });
  });

  it('gives a point event (BND/insertion, end === start) a real window instead of 1 bp', () => {
    const { locus } = buildStructuralVariantNavigation({
      familyId: 'F1',
      linkSearch: '',
      variant: variant({ start: 500_000, end: 500_000, type: 'INS' }),
    });

    expect(locus).toBe(`chr5:${500_000 - SV_FLANK_BP}-${500_000 + SV_FLANK_BP}`);
  });

  it('anchors the far edge on the greater coordinate when end precedes start', () => {
    const { locus } = buildStructuralVariantNavigation({
      familyId: 'F1',
      linkSearch: '',
      variant: variant({ start: 900_000, end: 800_000 }),
    });

    expect(locus).toBe(`chr5:${900_000 - SV_FLANK_BP}-${900_000 + SV_FLANK_BP}`);
  });

  it('clamps the near edge to the start of the chromosome', () => {
    const { locus, viewHref } = buildStructuralVariantNavigation({
      familyId: 'F1',
      linkSearch: '',
      variant: variant({ start: 10, end: 20 }),
    });

    // IGV loci are 1-based; the chromosome view accepts 0.
    expect(locus).toBe(`chr5:1-${20 + SV_FLANK_BP}`);
    expect(windowOf(viewHref).start).toBe(0);
  });

  it('normalises a bare chromosome name for IGV and strips the prefix for the route', () => {
    const { locus, igvHref, viewHref } = buildStructuralVariantNavigation({
      familyId: 'F1',
      linkSearch: '?type=DEL',
      projectId: 'P1',
      variant: variant({ chr: '7', start: 200_000, end: 200_500 }),
    });

    expect(locus.startsWith('chr7:')).toBe(true);
    expect(viewHref.startsWith('/families/F1/chromosome/7?')).toBe(true);
    expect(igvHref).toContain(`locus=${encodeURIComponent(locus)}`);
    expect(igvHref).toContain('project_id=P1');
    expect(viewHref).toContain('type=DEL');
  });
});
