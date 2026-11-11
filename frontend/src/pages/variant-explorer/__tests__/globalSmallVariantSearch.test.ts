import { act, renderHook } from '@testing-library/react';
import type { FormEvent } from 'react';
import { describe, expect, it } from 'vitest';

import { useGlobalSmallVariantSearchState } from '../globalSmallVariantSearch';

const cursorOf = (queryString: string): string | null =>
  new URLSearchParams(queryString).get('cursor');

type SearchState = { current: ReturnType<typeof useGlobalSmallVariantSearchState> };

// What the explorer asks the server for.
const requestOf = (result: SearchState) => new URLSearchParams(result.current.requestQueryString);

const submit = { preventDefault: () => undefined } as unknown as FormEvent;

describe('useGlobalSmallVariantSearchState keyset pagination', () => {
  it('starts on page 1 with no cursor', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    expect(result.current.pageNumber).toBe(1);
    expect(cursorOf(result.current.requestQueryString)).toBeNull();
    // The shared builder's offset `page` param, which the family pages page by, is not sent
    // with a keyset request.
    expect(new URLSearchParams(result.current.requestQueryString).has('page')).toBe(false);
  });

  it('walks forward through next cursors and back again', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());

    act(() => result.current.goToNextPage('C1'));
    expect(result.current.pageNumber).toBe(2);
    expect(cursorOf(result.current.requestQueryString)).toBe('C1');

    act(() => result.current.goToNextPage('C2'));
    expect(result.current.pageNumber).toBe(3);
    expect(cursorOf(result.current.requestQueryString)).toBe('C2');

    act(() => result.current.goToPreviousPage());
    expect(result.current.pageNumber).toBe(2);
    expect(cursorOf(result.current.requestQueryString)).toBe('C1');

    act(() => result.current.goToPreviousPage());
    expect(result.current.pageNumber).toBe(1);
    expect(cursorOf(result.current.requestQueryString)).toBeNull();
  });

  it('never pages before the first page', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    act(() => result.current.goToPreviousPage());
    expect(result.current.pageNumber).toBe(1);
  });

  it('resets to the first page when the sort changes (cursors would be stale)', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    act(() => result.current.goToNextPage('C1'));
    expect(result.current.pageNumber).toBe(2);

    act(() => result.current.setSort('position'));
    expect(result.current.pageNumber).toBe(1);
    expect(cursorOf(result.current.requestQueryString)).toBeNull();
  });

  // A cursor belongs to one search: whatever narrows or widens it starts again at page 1.
  it('restarts at page 1 for another assembly, imputed calls or genotype filter, and sends only genotype rows naming a sample', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());

    act(() => result.current.goToNextPage('C1'));
    act(() => result.current.setAssemblyId('asm-37'));
    expect(result.current.pageNumber).toBe(1);
    expect(requestOf(result).get('assembly_id')).toBe('asm-37');

    act(() => result.current.goToNextPage('C1'));
    act(() => result.current.setIncludeImputed(true));
    expect(result.current.pageNumber).toBe(1);
    expect(requestOf(result).get('include_imputed')).toBe('true');

    act(() => result.current.goToNextPage('C1'));
    act(() =>
      result.current.applyGenotypeFilters([
        { sample: 'PROBAND', mode: 'hom' },
        { sample: '', mode: 'het' },
      ]),
    );
    expect(result.current.pageNumber).toBe(1);
    expect(requestOf(result).getAll('sample_gt')).toEqual(['PROBAND:hom']);
  });
});

// A filter applies once applied, and a removed chip takes its filter off the search: a filter
// left in the request after its chip is gone would narrow the results unseen.
describe('useGlobalSmallVariantSearchState filters', () => {
  it('toggles the values of a multi-value filter in the draft, and searches with them only once applied', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    act(() => result.current.goToNextPage('C1'));

    act(() => result.current.toggleDraftFilterListValue('impact', 'HIGH'));
    act(() => result.current.toggleDraftFilterListValue('impact', 'MODERATE'));
    act(() => result.current.toggleDraftFilterListValue('impact', 'HIGH'));
    expect(result.current.draftFilters.impact).toBe('MODERATE');
    expect(requestOf(result).has('impact')).toBe(false);

    act(() => result.current.handleApply(submit));
    expect(requestOf(result).getAll('impact')).toEqual(['MODERATE']);
    expect(result.current.pageNumber).toBe(1);
  });

  it('takes a removed chip’s value, or its whole filter, off the search and the draft', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    act(() => {
      result.current.setDraftFilterValue('impact', 'HIGH, MODERATE');
      result.current.setDraftFilterValue('locus', '13:32316000-32317000');
    });
    act(() => result.current.handleApply(submit));
    expect(requestOf(result).getAll('impact')).toEqual(['HIGH', 'MODERATE']);
    expect(requestOf(result).get('chr')).toBe('13');
    const chip = (id: string) => {
      const found = result.current.activeFilterChips.find((candidate) => candidate.id === id);
      if (!found) throw new Error(`no chip ${id}`);
      return found;
    };

    act(() => result.current.goToNextPage('C1'));
    act(() => result.current.removeActiveFilterChip(chip('top:impact:HIGH')));
    expect(requestOf(result).getAll('impact')).toEqual(['MODERATE']);
    expect(result.current.draftFilters.impact).toBe('MODERATE');
    expect(result.current.pageNumber).toBe(1);

    act(() => result.current.removeActiveFilterChip(chip('top:locus')));
    for (const name of ['chr', 'start', 'end', 'gene', 'locus']) {
      expect(requestOf(result).has(name), name).toBe(false);
    }
    expect(result.current.draftFilters.locus).toBe('');
    expect(result.current.filters).toMatchObject({ chr: '', start: '', end: '', gene: '' });

    // A chip of a sample's genotype is the family pages'; here it changes nothing.
    const before = result.current.requestQueryString;
    act(() => result.current.removeActiveFilterChip({ id: 'gt:PROBAND', label: 'PROBAND', kind: 'sample-gt', sample: 'PROBAND' }));
    expect(result.current.requestQueryString).toBe(before);
  });

  it('clears every filter, applied and drafted, and starts again at page 1 on reset', () => {
    const { result } = renderHook(() => useGlobalSmallVariantSearchState());
    act(() => result.current.setDraftFilterValue('clinvar', 'Pathogenic'));
    act(() => result.current.handleApply(submit));
    act(() => result.current.setDraftFilterValue('impact', 'HIGH'));
    act(() => result.current.goToNextPage('C1'));

    act(() => result.current.handleReset());

    expect(requestOf(result).has('clinvar')).toBe(false);
    expect(result.current.draftFilters.impact).toBe('');
    expect(result.current.draftFilters.clinvar).toBe('');
    expect(result.current.pageNumber).toBe(1);
  });
});
