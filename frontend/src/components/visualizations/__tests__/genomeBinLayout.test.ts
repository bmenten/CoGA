import { describe, expect, it } from 'vitest';

import { deriveLayoutFromBins, splitKey } from '../genomeBinLayout';

describe('deriveLayoutFromBins', () => {
  it('lays the chromosomes end to end, each as long as its last bin', () => {
    const layout = deriveLayoutFromBins(
      [
        { chr: '1', end: 100 },
        { chr: '1', end: 250 },
        { chr: '2', end: 80 },
      ],
      ['1', '2', 'X'],
    );
    expect(layout.lengths).toEqual({ 1: 250, 2: 80, X: 0 });
    expect(layout.offsets).toEqual({ 1: 0, 2: 250, X: 330 });
    expect(layout.total).toBe(330);
  });

  it('spans only the region for one chromosome with a region', () => {
    const layout = deriveLayoutFromBins([{ chr: '7', end: 5_000 }], ['7'], 1_000, 3_000);
    expect(layout.total).toBe(2_000);
    expect(layout.offsets).toEqual({ 7: 0 });
  });
});

describe('splitKey', () => {
  it('splits a newline-joined chromosome key', () => {
    expect(splitKey('1\n2\nX')).toEqual(['1', '2', 'X']);
    expect(splitKey('')).toEqual([]);
  });
});
