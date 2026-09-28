export interface CsvExportInfo {
  /** File name to save under — marked when the export was cut at the cap. */
  filename: string;
  truncated: boolean;
  rows: number | null;
  limit: number | null;
}

type HeaderBag = Record<string, unknown> | undefined;

const headerNumber = (headers: HeaderBag, name: string): number | null => {
  const parsed = Number.parseInt(String(headers?.[name] ?? ''), 10);
  return Number.isFinite(parsed) ? parsed : null;
};

/**
 * Read a CSV export response's headers (#512). The backend caps an export and says so
 * in `X-CoGA-Export-Truncated`; a capped file is saved under a name that says so, and
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
  return {
    filename: truncated
      ? `${filenameStem}-TRUNCATED-first-${limit ?? rows ?? 'N'}.csv`
      : `${filenameStem}.csv`,
    truncated,
    rows,
    limit,
  };
}

export const truncatedExportMessage = (info: CsvExportInfo): string =>
  `The export was capped at ${
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
