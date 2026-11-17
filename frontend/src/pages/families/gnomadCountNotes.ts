import type { SmallVariant, SmallVariantPage } from './smallVariantSearch';

// A maximum gnomAD count filter keeps a variant whose annotation holds no such count, as
// absent from gnomAD (the SQL's ifNull(count, 0) <= maximum). For a variant gnomAD does not list
// that is right; for one it may list, the filter kept it unchecked, and a pipeline that writes
// gnomAD's frequencies but no counts gets every variant past it. The list says, in one line, how
// many of the variants it shows each filter kept that way: those without the count that have a
// gnomAD frequency, or whose callset was not annotated with gnomAD (nothing then tells that they
// are absent).
type CountFilter = {
  param: 'max_gnomad_ac' | 'max_gnomad_hom_count' | 'max_gnomad_hemi_count';
  // As the frequency section's header names the count.
  label: string;
  read: (variant: SmallVariant) => unknown;
  // Whether gnomAD has this count for the variant's position at all.
  applies: (variant: SmallVariant) => boolean;
};

const COUNT_FILTERS: CountFilter[] = [
  {
    param: 'max_gnomad_ac',
    label: 'AC',
    read: (variant) => variant.annotation_extra?.gnomad_ac,
    applies: () => true,
  },
  {
    param: 'max_gnomad_hom_count',
    label: 'H/H',
    read: (variant) => variant.gnomad_hom_count,
    applies: () => true,
  },
  {
    // A hemizygote count exists only where a male carries one copy: chrX and chrY outside the
    // pseudo-autosomal regions, which the backend marks (`hemizygous_in_males`).
    param: 'max_gnomad_hemi_count',
    label: 'hemi',
    read: (variant) => variant.annotation_extra?.gnomad_hemi_count,
    applies: (variant) => variant.hemizygous_in_males,
  },
];

/** What the line counts, for its hover text. */
export const GNOMAD_COUNT_NOTE_DETAIL =
  'A maximum gnomAD count filter keeps a variant without that count, as absent from gnomAD. ' +
  'Counted here: variants without the count that have a gnomAD frequency, or whose callset was ' +
  'not annotated with gnomAD. A hemizygote count exists only on chrX and chrY outside the ' +
  'pseudo-autosomal regions.';

// gnomAD's nuclear frequencies (combined, exomes, genomes, popmax, FAF95), not gnomAD-MT's.
const GNOMAD_FREQUENCY_KEY = /^gnomad_(?!mt_)[a-z0-9_]*af$/;

const mayBeInGnomad = (variant: SmallVariant): boolean =>
  typeof variant.gnomad_af === 'number' ||
  Object.entries(variant.population_frequencies ?? {}).some(
    ([key, value]) => GNOMAD_FREQUENCY_KEY.test(key) && typeof value === 'number',
  ) ||
  !variant.gnomad_annotated?.length;

// The variants a page shows: the single variants and the members of its pairs, each once.
const shownVariants = (page: SmallVariantPage): SmallVariant[] => {
  const byId = new Map<string, SmallVariant>();
  for (const variant of [...page.variants, ...(page.variant_groups ?? []).flatMap((group) => group.variants)]) {
    byId.set(variant._id, variant);
  }
  return [...byId.values()];
};

/** One line on the maximum gnomAD count filters the search applied (``requestQueryString``)
 * that kept shown variants unchecked, with how many per filter; null when none did. A variant
 * counts where gnomAD has the count (a hemizygote count only on chrX and chrY outside the PARs),
 * it has none, and it may be in gnomAD. */
export function gnomadCountNote(requestQueryString: string, page: SmallVariantPage | undefined): string | null {
  if (!page) return null;
  const variants = shownVariants(page);
  if (!variants.length) return null;
  const applied = new URLSearchParams(requestQueryString);
  const counts = COUNT_FILTERS.flatMap((filter) => {
    if (!applied.get(filter.param)?.trim()) return [];
    const unchecked = variants.filter(
      (variant) => filter.applies(variant) && typeof filter.read(variant) !== 'number' && mayBeInGnomad(variant),
    ).length;
    return unchecked ? [`${filter.label} ${unchecked}`] : [];
  });
  if (!counts.length) return null;
  const filters = counts.length === 1 ? 'filter' : 'filters';
  const shown = `${variants.length} ${variants.length === 1 ? 'variant' : 'variants'} shown`;
  return `Passed the gnomAD count ${filters} unchecked (no count, possibly in gnomAD): ${counts.join(', ')} of ${shown}.`;
}
