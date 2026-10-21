import { cssVar } from '../../lib/colors';
import type { VariantOut } from '../../lib/apiSchema.generated';
import { svKind } from '../../lib/svKind';

export { svKind };

// The rows the SV tracks draw, in this order: one per type, and OTHER, in the neutral colour,
// for an SV of a type none of them names. Every SV the list shows is drawn in one of them
// (CLIN-54).
export const SV_TYPE_ORDER = ['DEL', 'DUP', 'INV', 'INS', 'BND', 'OTHER'] as const;

export type SvTypeKey = (typeof SV_TYPE_ORDER)[number];

/**
 * Whether an SV removes a copy of what it spans ("loss"), adds one ("gain") or neither, as the
 * server reads it (`sv_copy_change.copy_change`): a DEL or DUP of any subtype by its type, a CNV
 * by its carriers' copy numbers, a male's single copy of chrX and chrY outside the PARs counted.
 * The track payload leaves it out when it is neither.
 */
export type SvCopyChange = VariantOut['copy_change'];

const TYPE_ROWS: ReadonlySet<string> = new Set(['DEL', 'DUP', 'INV', 'INS', 'BND']);

/**
 * The row an SV is drawn in (CLIN-54). A copy loss is drawn as a DEL and a copy gain as a DUP,
 * so a DEL:ME, a DUP:TANDEM and a CNV are drawn by the copies they change. Every other SV is
 * drawn by its type without subtypes (INS:ME as an INS), a TRA as a BND, and an SV of any other
 * type, or a CNV that changes no copy number, in the OTHER row. The tracks drew only the five
 * exact type names, so such an SV was not drawn at all, though the SV list showed it.
 */
export const svTypeKey = (type?: string | null, copyChange?: SvCopyChange): SvTypeKey => {
  if (copyChange === 'loss') return 'DEL';
  if (copyChange === 'gain') return 'DUP';
  const kind = svKind(type);
  if (kind === 'TRA') return 'BND';
  return TYPE_ROWS.has(kind) ? (kind as SvTypeKey) : 'OTHER';
};

/** A row's name in a track and in the Circos legend: its type, and "Other" for the OTHER row. */
export const svTypeLabel = (typeKey: SvTypeKey): string => (typeKey === 'OTHER' ? 'Other' : typeKey);

/**
 * An SV's type as a tooltip states it: as stored, and for a CNV, which names no direction,
 * the copy-number change it is drawn by.
 */
export const describeSvType = (type: string | null | undefined, copyChange?: SvCopyChange): string => {
  const stored = type ?? '';
  if (svKind(type) !== 'CNV') return stored;
  if (copyChange === 'loss') return `${stored} (copy-number loss)`;
  if (copyChange === 'gain') return `${stored} (copy-number gain)`;
  return `${stored} (no copy-number change)`;
};

/**
 * The theme colour of each row, read with cssVar() (a getComputedStyle flush), so a view
 * reads it once, in a memo. Shared by the SV tracks and the Circos plot, whose legend lists
 * the rows in this order; the OTHER row takes the neutral variant colour.
 */
export const svTypeColors = (): Record<SvTypeKey, string> => ({
  DEL: cssVar('--color-variant-del'),
  DUP: cssVar('--color-variant-dup'),
  INS: cssVar('--color-variant-ins'),
  INV: cssVar('--color-variant-inv'),
  BND: cssVar('--color-variant-bnd'),
  OTHER: cssVar('--color-variant-default'),
});

/** A row in an accessible name's count: its type, and "other" for the OTHER row. */
export const svTypeCountLabel = (typeKey: SvTypeKey): string => (typeKey === 'OTHER' ? 'other' : typeKey);

/** The drawn SVs in words, for the chart's accessible name (#529): "3 (2 DEL, 1 DUP)". */
export const describeSvTypes = (items: ReadonlyArray<{ typeKey: SvTypeKey }>): string => {
  const byType = SV_TYPE_ORDER.map((typeKey) => ({
    typeKey,
    count: items.filter((item) => item.typeKey === typeKey).length,
  }))
    .filter(({ count }) => count > 0)
    .map(({ typeKey, count }) => `${count.toLocaleString()} ${svTypeCountLabel(typeKey)}`);
  return `${items.length.toLocaleString()} (${byType.join(', ')})`;
};
