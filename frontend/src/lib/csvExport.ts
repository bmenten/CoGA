export interface CsvExportInfo {
  /** File name to save under — marked when the export was cut at the cap. */
  filename: string;
  truncated: boolean;
  /**
   * Why a truncated export is incomplete: it hit its row cap (`row-limit`, the file is the
   * first `limit` rows), or the search behind it read a capped candidate window
   * (`candidate-limit`: matches can be missing however few rows the file holds). Null
   * when the export is complete.
   */
  reason: CsvTruncationReason | null;
  rows: number | null;
  limit: number | null;
}

export type CsvTruncationReason = 'row-limit' | 'candidate-limit';

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
  // An older backend sends no reason: its only truncation was the row cap.
  let reason: CsvTruncationReason | null = null;
  if (truncated) {
    reason =
      String(headers?.['x-coga-export-truncated-reason'] ?? '') === 'candidate-limit'
        ? 'candidate-limit'
        : 'row-limit';
  }
  let filename = `${filenameStem}.csv`;
  if (reason === 'candidate-limit') filename = `${filenameStem}-TRUNCATED-partial-search.csv`;
  else if (reason === 'row-limit') filename = `${filenameStem}-TRUNCATED-first-${limit ?? rows ?? 'N'}.csv`;
  return { filename, truncated, reason, rows, limit };
}

export const truncatedExportMessage = (info: CsvExportInfo): string =>
  info.reason === 'candidate-limit'
    ? `The search behind this export read only part of the callset (its candidate limit was reached), so matching variants may be missing from the file${
        info.rows !== null ? ` (${info.rows.toLocaleString()} rows)` : ''
      } — it is incomplete. Narrow the filters (a region, a gene panel, tighter frequency or impact) to export everything.`
    : `The export was capped at ${
        info.limit !== null ? info.limit.toLocaleString() : 'its maximum number of'
      } rows — the filtered result is larger, so the file is incomplete. Narrow the filters to export everything.`;

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
