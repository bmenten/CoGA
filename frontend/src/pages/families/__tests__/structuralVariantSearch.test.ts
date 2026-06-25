import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import {
  STRUCTURAL_REF_GT_GROUP,
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
