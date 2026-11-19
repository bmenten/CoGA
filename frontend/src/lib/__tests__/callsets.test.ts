import { describe, expect, it } from 'vitest';

import { CALLER_LABELS, callsetLabel } from '../callsets';

// Each small-variant callset is named by the caller that made it (backend small_variant_callsets.py).
describe('the callsets a family holds', () => {
  it('names a callset of direct calls by its caller, any caller a package names included', () => {
    expect(callsetLabel('haplotypecaller')).toBe('Direct calls (GATK HaplotypeCaller)');
    expect(callsetLabel('deepvariant')).toBe('Direct calls (DeepVariant)');
    expect(callsetLabel('octopus')).toBe('Direct calls (octopus)');
    expect(Object.keys(CALLER_LABELS)).toEqual(['clair3', 'deepvariant', 'haplotypecaller', 'mutect2']);
  });

  it('names the callsets read by rules of their own', () => {
    expect(callsetLabel('nipt')).toBe('NIPT per-sample calls');
    expect(callsetLabel('glimpse2')).toBe('Imputed genotypes');
    expect(callsetLabel('mito')).toBe('mtDNA calls');
  });
});
