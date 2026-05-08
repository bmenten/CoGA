import { describe, expect, it } from 'vitest';
import { compareChromosomes } from '../chromosomes';

describe('compareChromosomes', () => {
  it('sorts chromosomes in natural order', () => {
    const input = ['chr2', 'chr1', 'chr10', 'chrX', 'chrY', 'MT'];
    const sorted = [...input].sort(compareChromosomes);
    expect(sorted).toEqual(['chr1', 'chr2', 'chr10', 'chrX', 'chrY', 'MT']);
  });

  it('handles values without chr prefix', () => {
    const input = ['2', '1', '10'];
    const sorted = [...input].sort(compareChromosomes);
    expect(sorted).toEqual(['1', '2', '10']);
  });
});

