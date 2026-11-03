import type { SmallVariantTagDefinition } from '../pages/families/smallVariantSearch';
import type { GlobalVariantRow } from '../pages/variant-explorer/types';
import { tagDefinition } from './tagDefinitions';

/**
 * A row of the variant explorer's results (GlobalVariantRow), the fields a test leaves out as
 * they are for BRCA2's missense 13-32316461-G-A (c.7007G>A, ClinVar pathogenic, no lab
 * classification or tags), carried by 5 samples, 3 het and 2 hom, in 4 families.
 */
export const globalVariantRow = (overrides: Partial<GlobalVariantRow> = {}): GlobalVariantRow => ({
  key: '101',
  variant_id: '13-32316461-G-A',
  chr: '13',
  pos: 32316461,
  ref: 'G',
  alt: 'A',
  rsid: null,
  type: 'SNV',
  gene: 'BRCA2',
  gene_symbols: ['BRCA2'],
  impact: 'MODERATE',
  consequence: 'missense_variant',
  effects: ['missense_variant'],
  hgvsc: 'c.7007G>A',
  hgvsp: 'p.Arg2336His',
  clinvar: 'Pathogenic',
  classification: null,
  tags: [],
  total_samples: 5,
  het_samples: 3,
  hom_samples: 2,
  total_families: 4,
  last_seen: null,
  ...overrides,
});

/** The built-in Review tag, as the explorer's tag list serves it. */
export const REVIEW_TAG: SmallVariantTagDefinition = tagDefinition({
  key: 'review',
  label: 'Review',
  group: 'collaboration',
  color: '#2563eb',
  sort_order: 10,
  scope: 'system',
  is_custom: false,
});
