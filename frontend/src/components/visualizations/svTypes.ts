import { cssVar } from '../../lib/colors';

// The structural-variant types the SV tracks draw, one row each, in this order.
export const SV_TYPE_ORDER = ['DEL', 'DUP', 'INV', 'INS', 'BND'] as const;

export type SvTypeKey = (typeof SV_TYPE_ORDER)[number];

/**
 * The theme colour of each SV type, read with cssVar() (a getComputedStyle flush), so a view
 * reads it once, in a memo. Shared by the genome SV track and the Circos plot, whose legend
 * lists the types in this order.
 */
export const svTypeColors = (): Record<string, string> => ({
  DEL: cssVar('--color-variant-del'),
  DUP: cssVar('--color-variant-dup'),
  INS: cssVar('--color-variant-ins'),
  INV: cssVar('--color-variant-inv'),
  BND: cssVar('--color-variant-bnd'),
});

/** The drawn SVs in words, for the chart's accessible name (#529): "3 (2 DEL, 1 DUP)". */
export const describeSvTypes = (items: ReadonlyArray<{ typeKey: SvTypeKey }>): string => {
  const byType = SV_TYPE_ORDER.map((typeKey) => ({
    typeKey,
    count: items.filter((item) => item.typeKey === typeKey).length,
  }))
    .filter(({ count }) => count > 0)
    .map(({ typeKey, count }) => `${count.toLocaleString()} ${typeKey}`);
  return `${items.length.toLocaleString()} (${byType.join(', ')})`;
};
