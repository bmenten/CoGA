import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  STRUCTURAL_ALL_GT_GROUPS,
  STRUCTURAL_REF_GT_GROUP,
  buildStructuralPresetPayload,
  buildStructuralVariantQueryParams,
  cloneSingleSampleFilter,
  createEmptyStructuralFilters,
  sortStructuralVariants,
  structuralLocationProblem,
  structuralVariantRowKey,
  useStructuralVariantSearchState,
} from '../structuralVariantSearch';
import { countPresetRules } from '../smallVariantSearch';

const family = {
  members: [
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
  ],
  relationships: [],
  projects: [],
} as never;

describe('useStructuralVariantSearchState default (fresh open)', () => {
  it('defaults to the Mendeliome SV view: panel + AF < 1% + affected carriers', async () => {
    const { result } = renderHook(() =>
      useStructuralVariantSearchState({
        family,
        locationSearch: '',
        navigate: () => {},
        mendeliomePanelId: 'mendel-1',
        panelsLoaded: true,
      }),
    );
    await waitFor(() => expect(result.current.filters.panel_id).toBe('mendel-1'));
    expect(result.current.filters.max_population_af).toBe('0.01');
    // Affected individual is required to carry the SV (het/hom, not reference-only).
    const probandGt = result.current.sampleFilters.PROBAND?.gt ?? [];
    expect(probandGt.length).toBeGreaterThan(0);
    expect(probandGt.some((gt) => STRUCTURAL_REF_GT_GROUP.includes(gt))).toBe(false);
  });

  it('does not override a deep-linked search', async () => {
    const { result } = renderHook(() =>
      useStructuralVariantSearchState({
        family,
        locationSearch: '?gene=BRCA1',
        navigate: () => {},
        mendeliomePanelId: 'mendel-1',
        panelsLoaded: true,
      }),
    );
    await waitFor(() => expect(result.current.filters.gene).toBe('BRCA1'));
    expect(result.current.filters.panel_id).toBe('');
  });

  it('waits for the panels query before applying the default', async () => {
    const { result, rerender } = renderHook(
      ({ loaded, id }: { loaded: boolean; id?: string }) =>
        useStructuralVariantSearchState({
          family,
          locationSearch: '',
          navigate: () => {},
          mendeliomePanelId: id,
          panelsLoaded: loaded,
        }),
      { initialProps: { loaded: false, id: undefined } as { loaded: boolean; id?: string } },
    );
    expect(result.current.filters.panel_id).toBe('');
    rerender({ loaded: true, id: 'mendel-1' });
    await waitFor(() => expect(result.current.filters.panel_id).toBe('mendel-1'));
  });
});

// The clone fills `filter.gt` and the rest of a partial filter, never throws on one.
describe('cloneSingleSampleFilter', () => {
  it('fills a partial filter with the defaults instead of throwing', () => {
    expect(cloneSingleSampleFilter({ qual: '20' })).toEqual({
      gt: [...STRUCTURAL_ALL_GT_GROUPS],
      qual: '20',
      read_support: '',
      filter: '',
    });
    expect(cloneSingleSampleFilter(null).gt).toEqual([...STRUCTURAL_ALL_GT_GROUPS]);
  });

  it('copies the genotype selection rather than sharing it', () => {
    const original = { gt: ['1/1'], qual: '', read_support: '5', filter: 'PASS' };
    const copy = cloneSingleSampleFilter(original);
    copy.gt.push('0/1');
    expect(original.gt).toEqual(['1/1']);
    expect(copy.read_support).toBe('5');
  });

  it('builds a preset payload from a sample filter without a genotype selection', () => {
    const payload = buildStructuralPresetPayload({
      filters: {} as never,
      members: [{ sample_id: 'PROBAND', role: 'proband' }] as never,
      sampleFilters: { PROBAND: { qual: '30' } as never },
    });
    expect(payload.sample_filters.PROBAND.gt).toEqual([...STRUCTURAL_ALL_GT_GROUPS]);
    expect(payload.sample_filters.PROBAND.qual).toBe('30');
  });

  // The preset list counts a preset's rules, not each member's role, which an SV preset keeps
  // among its templates.
  it('counts the rules of a saved preset, not its members', () => {
    const payload = buildStructuralPresetPayload({
      filters: {} as never,
      members: [
        { sample_id: 'FATHER', role: 'father' },
        { sample_id: 'MOTHER', role: 'mother' },
        { sample_id: 'PROBAND', role: 'proband' },
      ] as never,
      sampleFilters: { PROBAND: { qual: '30' } as never },
    });
    expect(Object.keys(payload.sample_templates)).toHaveLength(3);
    expect(countPresetRules({ ...payload, filters: { type: ['DEL'] } })).toBe(2);
    // A small-variant preset's templates hold filters: each is a rule.
    expect(countPresetRules({ filters: {}, sample_filters: {}, sample_templates: { all: { gt: ['het'] } } })).toBe(1);
  });
});

// A location that reads as neither a gene nor a region is named, not searched: sent on as a
// gene name, it would match nothing and read as a family without SVs.
describe('a location that cannot be read', () => {
  it('names the problem, or none', () => {
    expect(structuralLocationProblem({ locus: 'chr1:100-' })).toBe(
      "Location 'chr1:100-' is not a gene or chr:start-end.",
    );
    expect(structuralLocationProblem({ locus: 'chr1:1,000–2,000' })).toBeNull();
    expect(structuralLocationProblem({ locus: 'BRCA1' })).toBeNull();
    expect(structuralLocationProblem({ locus: '' })).toBeNull();
  });

  it('is not applied from the draft, and says why', async () => {
    const navigate = vi.fn();
    const { result } = renderHook(() =>
      useStructuralVariantSearchState({ family, locationSearch: '?gene=BRCA1', navigate, panelsLoaded: true }),
    );
    await waitFor(() => expect(result.current.filters.gene).toBe('BRCA1'));
    navigate.mockClear();

    act(() => result.current.setDraftFilterValue('locus', '1:-200'));
    act(() => result.current.handleSearch({ preventDefault: () => {} } as never));

    expect(result.current.draftLocationProblem).toBe("Location '1:-200' is not a gene or chr:start-end.");
    expect(result.current.filters.locus).toBe('');
    expect(navigate).not.toHaveBeenCalled();
  });
});

describe('structuralVariantRowKey', () => {
  it('tells two callers of one SV id apart and keeps one caller stable', () => {
    const id = '1-30000-31000-DEL---';
    expect(structuralVariantRowKey({ _id: id, source: 'sniffles' })).not.toBe(
      structuralVariantRowKey({ _id: id, source: 'spectre' }),
    );
    expect(structuralVariantRowKey({ _id: id, source: 'sniffles' })).toBe(
      structuralVariantRowKey({ _id: id, source: 'sniffles' }),
    );
    expect(structuralVariantRowKey({ _id: id, source: null })).toBe(`${id}|`);
  });
});

// The location filters and the region flags as the SV search sends them.
describe('buildStructuralVariantQueryParams', () => {
  const query = (overrides: Partial<ReturnType<typeof createEmptyStructuralFilters>>) =>
    buildStructuralVariantQueryParams({ ...createEmptyStructuralFilters(), ...overrides }, {}, 1, true).toString();

  it('sends a region locus with the chromosome, start and end it reads as', () => {
    expect(query({ locus: 'chr1:1,000-2,000', gene: 'BRCA2' })).toBe(
      'page=1&page_size=100&locus=chr1%3A1%2C000-2%2C000&chr=1&start=1000&end=2000&prioritize=true',
    );
  });

  it('without a locus, sends the separate fields, and each region flag on its own', () => {
    expect(query({ gene: 'BRCA2', chr: '13', start: '100', end: '200', region_flags: ' Segdup , Repeat,' })).toBe(
      'page=1&page_size=100&gene=BRCA2&chr=13&start=100&end=200&region_flag=Segdup&region_flag=Repeat' +
        '&prioritize=true',
    );
  });

  // REQ-PGT-007: the types ticked, as the form joins them, and each length bound under the
  // name the API reads it by.
  it('sends the SV types and the length bounds', () => {
    expect(query({ type: 'DEL, DUP', minLength: '10000000' })).toBe(
      'page=1&page_size=100&min_length=10000000&type=DEL%2C+DUP&prioritize=true',
    );
    expect(query({ length: '5000' })).toBe('page=1&page_size=100&length=5000&prioritize=true');
  });

  // The ranking's order: sent for a prioritised search ranked by the variant score only.
  it('sends the ranking order of a prioritised search only', () => {
    expect(query({ rank_by: 'variant_score' })).toBe('page=1&page_size=100&prioritize=true&rank_by=variant_score');
    expect(query({ rank_by: '' })).toBe('page=1&page_size=100&prioritize=true');
    expect(query({ prioritize: '', rank_by: 'variant_score' })).toBe('page=1&page_size=100');
  });
});

it.each([
  ['?prioritize=true&rank_by=variant_score', 'variant_score'],
  ['?prioritize=true&rank_by=anything', ''],
])('reads the ranking order of a link %s', async (search, rankBy) => {
  const { result } = renderHook(() =>
    useStructuralVariantSearchState({
      family,
      locationSearch: search,
      navigate: () => {},
      mendeliomePanelId: 'mendel-1',
      panelsLoaded: true,
    }),
  );
  await waitFor(() => expect(result.current.filters.prioritize).toBe('true'));
  expect(result.current.filters.rank_by).toBe(rankBy);
});

// The Score column's order is the ranking's: by the rank the server gave each SV, whichever
// score it ranked by, not by the combined score the cell may not show.
describe('sortStructuralVariants by priority', () => {
  const sv = (id: string, combined: number, variant: number, rank: number | null) =>
    ({
      _id: id,
      chr: '1',
      start: 1,
      end: 2,
      type: 'DEL',
      priority:
        rank === null
          ? null
          : {
              combined_score: combined,
              variant_score: variant,
              pathogenicity_score: variant,
              frequency_score: 1,
              segregation_weight: 1,
              phenotype_score: null,
              segregation_modes: [],
              phenotype_gene: null,
              phenotype_matches: [],
              rank,
            },
    }) as never;

  it('lists the SVs in rank order, the unranked last, and the toggle flips it', () => {
    const variants = [sv('unranked', 0, 0, null), sv('second', 0.9, 0.3, 2), sv('first', 0.4, 0.8, 1)];
    const ids = (asc: boolean) =>
      sortStructuralVariants(variants, 'priority', asc).map((variant) => variant._id);
    expect(ids(true)).toEqual(['first', 'second', 'unranked']);
    expect(ids(false)).toEqual(['unranked', 'second', 'first']);
  });
});
