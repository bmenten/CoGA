import { renderHook } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { useSameSpanFallbackData } from '../useSameSpanFallbackData';

type Args = { data: string[] | null; span: number; scope: string; failed?: boolean };

const setup = (initial: Args) =>
  renderHook(
    (args: Args) => useSameSpanFallbackData(args.data, args.span, args.scope, args.failed),
    { initialProps: initial },
  );

describe('useSameSpanFallbackData', () => {
  it('returns live data and holds it across a pan of the same span and scope', () => {
    const { result, rerender } = setup({ data: ['a'], span: 100, scope: 'F1|chr1' });
    expect(result.current).toEqual(['a']);

    rerender({ data: null, span: 100, scope: 'F1|chr1' });
    expect(result.current).toEqual(['a']);
  });

  it('drops held data on a zoom (different span)', () => {
    const { result, rerender } = setup({ data: ['a'], span: 100, scope: 'F1|chr1' });
    rerender({ data: null, span: 200, scope: 'F1|chr1' });
    expect(result.current).toBeNull();
  });

  it('never shows one chromosome’s data under another at the same zoom (#510)', () => {
    const { result, rerender } = setup({ data: ['chr1 blocks'], span: 100, scope: 'F1|chr1' });
    rerender({ data: null, span: 100, scope: 'F1|chr2' });
    expect(result.current).toBeNull();
  });

  it('never falls back to held data on a failure (#586)', () => {
    const { result, rerender } = setup({ data: ['window 1'], span: 100, scope: 'F1|chr1' });

    // A pan whose request failed: same span and scope, no data. It used to return the
    // previous window, drawn under the error overlay against the new region.
    rerender({ data: null, span: 100, scope: 'F1|chr1', failed: true });
    expect(result.current).toBeNull();

    // Nor does a failed refetch that still carries the last good answer.
    rerender({ data: ['window 1'], span: 100, scope: 'F1|chr1', failed: true });
    expect(result.current).toBeNull();

    // Loading again (a retry or the next pan) may glide on what was last shown.
    rerender({ data: null, span: 100, scope: 'F1|chr1' });
    expect(result.current).toEqual(['window 1']);
  });
});
