import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  STRUCTURAL_ALL_GT_GROUPS,
  STRUCTURAL_REF_GT_GROUP,
  buildStructuralPresetPayload,
  cloneSingleSampleFilter,
  useStructuralVariantSearchState,
} from '../structuralVariantSearch';

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

// The clone used to spread `filter.gt` unguarded, so a partial filter threw (#528).
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
});
