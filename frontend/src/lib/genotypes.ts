/**
 * One classification of a VCF genotype, matching the backend's services/genotypes.py
 * (#511). Genotypes arrive as the VCF wrote them, so a haploid call ('1', '0' — chrM, and
 * chrX/chrY in males from callers that emit ploidy-1 calls) and a multi-allelic one
 * ('1/2', '2/2') must be read like any other rather than falling through a diploid,
 * biallelic list.
 *
 * - hom_alt: every allele called and the same ALT allele — '1/1', '2|2', haploid '1'.
 * - het: at least one ALT allele and not hom_alt — '0/1', '1/2', and a half call './1'.
 * - hom_ref: every allele '0' — '0/0', haploid '0'.
 * - no_call: everything else — './.', '.', '', a half reference call './0'.
 */
export type GenotypeClass = 'hom_alt' | 'het' | 'hom_ref' | 'no_call';

const isAllele = (value: string) => value === '.' || value === '' || /^[0-9]+$/.test(value);
const isMissing = (value: string) => value === '.' || value === '';

export function classifyGenotype(gt?: string | null): GenotypeClass {
  const raw = (gt ?? '').trim();
  if (!raw) return 'no_call';
  const alleles = raw.split(/[/|]/);
  if (!alleles.every(isAllele)) return 'no_call';
  if (alleles.every((allele) => allele === '0')) return 'hom_ref';
  if (!alleles.some((allele) => !isMissing(allele) && allele !== '0')) return 'no_call';
  if (!alleles.some(isMissing) && new Set(alleles).size === 1) return 'hom_alt';
  return 'het';
}

const GENOTYPE_LABELS: Record<GenotypeClass, string> = {
  hom_alt: 'Hom',
  het: 'Het',
  hom_ref: 'WT',
  no_call: 'No call',
};

export function formatGt(gt?: string): string {
  return GENOTYPE_LABELS[classifyGenotype(gt)];
}

// True only when the genotype carries at least one alt allele (Het or Hom).
// No-call and reference both return false, so carrier/presence filters must use
// this rather than `formatGt(gt) !== 'WT'` — that check counts no-calls, which the
// backend buckets apart from carriers.
export function hasAltAllele(gt?: string): boolean {
  const cls = classifyGenotype(gt);
  return cls === 'het' || cls === 'hom_alt';
}

/** The zygosity CSS modifier used by the variant cards' genotype lines. */
export function genotypeZygosity(gt?: string | null): 'na' | 'ref' | 'hom' | 'het' {
  const cls = classifyGenotype(gt);
  if (cls === 'hom_alt') return 'hom';
  if (cls === 'het') return 'het';
  if (cls === 'hom_ref') return 'ref';
  return 'na';
}

/**
 * What each genotype toggle in the filter forms covers. The backend reads a group as its
 * genotype class, so the hint names the calls a literal list would not show (#511).
 */
export const GENOTYPE_GROUP_HINTS = {
  'hom-group': 'Homozygous alt, including hemizygous/haploid calls (1) and 2/2 at multi-allelic sites',
  'het-group': 'Heterozygous, including multi-allelic calls such as 1/2 and half calls such as ./1',
  'ref-group': 'Reference, including haploid 0, no-calls and samples without a call at the site',
} as const;
