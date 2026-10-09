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
 * A date with its month abbreviated, in the viewer's locale: "Jan 15, 2024" in English. A
 * missing value, and one that does not parse, shows an em dash.
 */
export const formatShortDate = (value?: string | null): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date);
};

/**
 * A count with the locale's digit grouping: 12345 → "12,345". It uses Intl.NumberFormat, so a
 * count a payload left out renders "NaN" rather than throwing as `toLocaleString` would.
 */
export const formatCount = (value: number): string => new Intl.NumberFormat().format(value);

/**
 * A length of time: seconds under a minute ("12 s"), minutes under an hour ("25 min"), then
 * hours and minutes ("2 h 5 min") and days and hours ("1 d 3 h"), rounded to the last unit.
 */
export const formatDuration = (seconds: number): string => {
  const total = Math.max(0, seconds);
  if (Math.round(total) < 60) return `${Math.round(total)} s`;
  const minutes = Math.round(total / 60);
  if (minutes < 60) return `${minutes} min`;
  if (minutes < 24 * 60) {
    const hours = Math.floor(minutes / 60);
    return minutes % 60 ? `${hours} h ${minutes % 60} min` : `${hours} h`;
  }
  const hours = Math.round(minutes / 60);
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days} d ${hours % 24} h` : `${days} d`;
};

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
