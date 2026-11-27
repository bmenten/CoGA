import type { ReactNode } from 'react';
import type { SmallVariant } from './smallVariantSearch';
import { formatVariantChange } from './smallVariantResultUtils';

/** How a group lists its variants: each as its full card, or as the rows of a table. */
export type MemberView = 'cards' | 'table';

/** What a kind of group (`SmallVariantGroupCards`) shows of its own around its variants. */
export interface GroupView {
  /** The kind, as the card's heading labels it, its folding toggle names it, and its aria label. */
  label: string;
  subject: string;
  aria: string;
  /** The chips beside the heading's variant count. */
  chips: ReactNode;
  /** The lines under the gene's: why the gene is listed and what each person carries. */
  lines: ReactNode;
  /** On each variant's card, in the lines' colours: who carries it. */
  personChips: (variant: SmallVariant) => ReactNode;
}

export const listInWords = (items: string[]) =>
  items.length === 1 ? items[0] : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;

// Some of a group's variants, as its lines name them: by their place when the group lists its
// variants as cards headed "Variant 1 of 3", by their change in a table.
export const namedVariants = (variants: SmallVariant[], ids: string[], memberView: MemberView) => {
  const named = variants
    .map((variant, index) => ({ variant, place: index + 1 }))
    .filter(({ variant }) => ids.includes(variant._id));
  if (!named.length) return null;
  if (memberView === 'table') return listInWords(named.map(({ variant }) => formatVariantChange(variant)));
  return `${named.length === 1 ? 'variant' : 'variants'} ${listInWords(named.map(({ place }) => String(place)))}`;
};
