/**
 * An OMIM search for a disease or gene name, for a finding whose source gives no OMIM entry
 * (the repeat catalogue, MITOMAP's disorders): the analyst picks the entry from the hits.
 */
export const omimSearchHref = (term: string): string =>
  `https://www.omim.org/search?index=entry&search=${encodeURIComponent(term)}`;

/** The gene's OMIM search, the "OMIM" link in every finding card's link row. */
export const omimGeneHref = (gene: string): string =>
  `https://www.omim.org/search?search=${encodeURIComponent(gene)}`;
