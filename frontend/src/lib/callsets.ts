/**
 * The small-variant callsets a family holds, each named by the caller that made it (backend
 * `small_variant_callsets.py`): `haplotypecaller` (short-read WGS), `mutect2` (a NIPT plasma),
 * `clair3` and `deepvariant` (long-read WGS), or any other caller a package names; beside them
 * the imputed genotypes (`glimpse2`), the mtDNA calls (`mito`) and a monogenic NIPT's per-sample
 * calls (`nipt`).
 */
export const CALLER_LABELS: Record<string, string> = {
  clair3: 'Clair3',
  deepvariant: 'DeepVariant',
  haplotypecaller: 'GATK HaplotypeCaller',
  mutect2: 'GATK Mutect2',
};

/** A callset in words: a caller's direct calls by the caller's name. */
export const callsetLabel = (callset: string): string => {
  if (callset === 'nipt') return 'NIPT per-sample calls';
  if (callset === 'glimpse2' || callset === 'shapeit') return 'Imputed genotypes';
  if (callset === 'mito') return 'mtDNA calls';
  return `Direct calls (${CALLER_LABELS[callset] ?? callset})`;
};
