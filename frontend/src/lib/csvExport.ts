export interface CsvExportInfo {
  /** File name to save under — marked when the export was cut at the cap. */
  filename: string;
  truncated: boolean;
  /**
   * Why a truncated export is incomplete: it hit its row cap (`row-limit`, the file is the
   * first `limit` rows), the search behind it read a capped candidate window
   * (`candidate-limit`), or its tag or classification filter matched more variants than the
   * search takes (`review-filter-limit`, the Variant Explorer); in the last two, matches can
   * be missing however few rows the file holds. Null when the export is complete.
   */
  reason: CsvTruncationReason | null;
  rows: number | null;
  limit: number | null;
}

export type CsvTruncationReason = 'row-limit' | 'candidate-limit' | 'review-filter-limit';

// The reasons that make the search itself partial: a match can be missing anywhere in the file.
const isPartialSearchReason = (value: string): value is 'candidate-limit' | 'review-filter-limit' =>
  value === 'candidate-limit' || value === 'review-filter-limit';

type HeaderBag = Record<string, unknown> | undefined;

const headerNumber = (headers: HeaderBag, name: string): number | null => {
  const parsed = Number.parseInt(String(headers?.[name] ?? ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
};

/**
 * Read a CSV export response's headers (#512). The backend caps an export and says so
 * in `X-CoGA-Export-Truncated`, and why in `X-CoGA-Export-Truncated-Reason`; a capped file is saved under a name that says so, and
 * the caller tells the user, so a partial export can never pass for the full result.
 */
export function describeCsvExport(
  response: { headers?: HeaderBag },
  filenameStem: string,
): CsvExportInfo {
  const headers = response.headers;
  const truncated = String(headers?.['x-coga-export-truncated'] ?? '') === 'true';
  const rows = headerNumber(headers, 'x-coga-export-rows');
  const limit = headerNumber(headers, 'x-coga-export-limit');
  // A truncation without a stated reason is the row cap, as on the server (core/csv_export.py).
  let reason: CsvTruncationReason | null = null;
  if (truncated) {
    const stated = String(headers?.['x-coga-export-truncated-reason'] ?? '');
    reason = isPartialSearchReason(stated) ? stated : 'row-limit';
  }
  let filename = `${filenameStem}.csv`;
  if (reason === 'row-limit') filename = `${filenameStem}-TRUNCATED-first-${limit ?? rows ?? 'N'}.csv`;
  else if (reason !== null) filename = `${filenameStem}-TRUNCATED-partial-search.csv`;
  return { filename, truncated, reason, rows, limit };
}

export const truncatedExportMessage = (info: CsvExportInfo): string => {
  const rowCount = info.rows !== null ? ` (${info.rows.toLocaleString()} rows)` : '';
  if (info.reason === 'candidate-limit') {
    return `The search behind this export read only part of the callset (its candidate limit was reached), so matching variants may be missing from the file${rowCount} — it is incomplete. Narrow the filters (a region, a gene panel, tighter frequency or impact) to export everything.`;
  }
  if (info.reason === 'review-filter-limit') {
    return `The tag or classification filter matched more variants than one search can take, so matching variants may be missing from the file${rowCount} — it is incomplete. Filter on fewer tags or classifications to export everything.`;
  }
  return `The export was capped at ${
    info.limit !== null ? info.limit.toLocaleString() : 'its maximum number of'
  } rows — the filtered result is larger, so the file is incomplete. Narrow the filters to export everything.`;
};

/** Save a downloaded CSV under `filename`. */
export function saveCsvBlob(data: BlobPart, filename: string): void {
  const url = window.URL.createObjectURL(new Blob([data], { type: 'text/csv' }));
  const anchor = document.createElement('a');
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(url);
}
