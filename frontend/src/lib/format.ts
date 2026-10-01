// Display formatters shared across pages. Each page used to carry its own copy; the
// copies had the same behaviour and differed only in the text shown for a missing value,
// which is now an argument.

/**
 * A timestamp in the viewer's locale. A missing value shows `missing`; a value that does not
 * parse is shown as it came.
 */
export const formatDateTime = (value?: string | null, missing = ''): string => {
  if (!value) return missing;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

/** {@link formatDateTime} with an em dash for a missing timestamp. */
export const formatTimestamp = (value?: string | null): string => formatDateTime(value, '—');

/**
 * A date in the viewer's locale. A date-only value (`2026-06-06`) is parsed as UTC midnight,
 * which in local time falls a day early west of UTC, so it is shown in UTC (#526).
 */
export const formatDate = (value?: string | null, missing = ''): string => {
  if (!value) return missing;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return /^\d{4}-\d{2}-\d{2}$/.test(value)
    ? date.toLocaleDateString(undefined, { timeZone: 'UTC' })
    : date.toLocaleDateString();
};

/**
 * A count with the locale's digit grouping: 12345 → "12,345". It uses Intl.NumberFormat, so a
 * count a payload left out renders "NaN" rather than throwing as `toLocaleString` would.
 */
export const formatCount = (value: number): string => new Intl.NumberFormat().format(value);

/** A genomic span in bp, kb (one decimal) or Mb (two decimals). */
export const formatRegionSize = (size: number): string => {
  if (size >= 1_000_000) return `${(size / 1_000_000).toFixed(2)} Mb`;
  if (size >= 1_000) return `${(size / 1_000).toFixed(1)} kb`;
  return `${size} bp`;
};

/** "a", "a and b", "a, b and c"; empty entries are left out. */
export function joinWithAnd(items: string[]): string {
  const filtered = items.filter(Boolean);
  if (filtered.length === 0) return '';
  if (filtered.length === 1) return filtered[0];
  return `${filtered.slice(0, -1).join(', ')} and ${filtered[filtered.length - 1]}`;
}
