import type { SmallVariant, SmallVariantPage } from './smallVariantSearch';

// A maximum gnomAD count filter keeps a variant whose annotation holds no such count, as
// absent from gnomAD (the SQL's ifNull(count, 0) <= maximum). A pipeline that writes gnomAD's
// frequencies but no counts therefore gets every variant past it, without a word: the list
// says so for the variants it shows (CLIN-67).
type CountFilter = {
  param: 'max_gnomad_ac' | 'max_gnomad_hom_count' | 'max_gnomad_hemi_count';
  filterLabel: string;
  countLabel: string;
  read: (variant: SmallVariant) => unknown;
};

const COUNT_FILTERS: CountFilter[] = [
  {
    param: 'max_gnomad_ac',
    filterLabel: 'Max gnomAD AC',
    countLabel: 'gnomAD allele count',
    read: (variant) => variant.annotation_extra?.gnomad_ac,
  },
  {
    param: 'max_gnomad_hom_count',
    filterLabel: 'Max gnomAD H/H',
    countLabel: 'gnomAD homozygote count',
    read: (variant) => variant.gnomad_hom_count,
  },
  {
    param: 'max_gnomad_hemi_count',
    filterLabel: 'Max gnomAD hemi',
    countLabel: 'gnomAD hemizygote count',
    read: (variant) => variant.annotation_extra?.gnomad_hemi_count,
  },
];

// The variants a page shows: the single variants and the members of its pairs, each once.
const shownVariants = (page: SmallVariantPage): SmallVariant[] => {
  const byId = new Map<string, SmallVariant>();
  for (const variant of [...page.variants, ...(page.variant_groups ?? []).flatMap((group) => group.variants)]) {
    byId.set(variant._id, variant);
  }
  return [...byId.values()];
};

const sentence = (filter: CountFilter, missing: number, shown: number): string => {
  const kept = `the ${filter.filterLabel} filter keeps ${missing === 1 ? 'it' : 'them'}, as absent from gnomAD`;
  if (shown === 1) return `The variant shown has no ${filter.countLabel}: ${kept}.`;
  if (missing === shown) return `None of the ${shown} variants shown has a ${filter.countLabel}: ${kept}.`;
  return `${missing} of the ${shown} variants shown ${missing === 1 ? 'has' : 'have'} no ${filter.countLabel}: ${kept}.`;
};

/** A sentence for each maximum gnomAD count filter the search applied (``requestQueryString``)
 * that the page shows variants without that count for: how many, and that the filter kept them. */
export function gnomadCountNotes(requestQueryString: string, page: SmallVariantPage | undefined): string[] {
  if (!page) return [];
  const variants = shownVariants(page);
  if (!variants.length) return [];
  const applied = new URLSearchParams(requestQueryString);
  return COUNT_FILTERS.flatMap((filter) => {
    if (!applied.get(filter.param)?.trim()) return [];
    const missing = variants.filter((variant) => typeof filter.read(variant) !== 'number').length;
    return missing ? [sentence(filter, missing, variants.length)] : [];
  });
}
