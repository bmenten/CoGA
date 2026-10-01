import React from 'react';
import { joinWithAnd } from '../lib/format';

/** What a partly failed family-package import left out, as the family's metadata records it. */
export interface FamilyImportIncomplete {
  /** When the import ran (ISO timestamp), or null when not recorded. */
  at: string | null;
  failedDatasets: string[];
  importedDatasets: string[];
  /** The import job whose record holds each dataset's error, or null when not recorded. */
  jobId: string | null;
}

const stringList = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : null;

/**
 * The family's `import_incomplete` flag, or null when its import completed.
 *
 * A family-package import that partly fails keeps the datasets that did import and stamps
 * `metadata.import_incomplete` (family_package_registration._flag_family_import_incomplete);
 * a later, fully successful import removes it. As on the server, a flag that is set but
 * not in the shape the import writes still counts as incomplete, with nothing to name,
 * and a flag written before the import job was recorded has no job to point to.
 */
export const importIncompleteFromMetadata = (metadata: unknown): FamilyImportIncomplete | null => {
  if (!metadata || typeof metadata !== 'object') return null;
  const flag = (metadata as Record<string, unknown>).import_incomplete;
  if (flag === undefined || flag === null || flag === false) return null;
  const record =
    typeof flag === 'object' && !Array.isArray(flag) ? (flag as Record<string, unknown>) : {};
  return {
    at: typeof record.at === 'string' && record.at ? record.at : null,
    failedDatasets: stringList(record.failed_datasets) ?? [],
    importedDatasets: stringList(record.imported_datasets) ?? [],
    jobId: typeof record.job_id === 'string' && record.job_id ? record.job_id : null,
  };
};

/**
 * "Import incomplete" — shown on every family page while the family's data only partly
 * loaded, naming what failed. Printed with the report, like the assembly-scope label.
 * Sign-out refuses such a family unless the signer acknowledges it with a reason,
 * whatever this banner does.
 */
const ImportIncompleteBanner: React.FC<{ metadata?: unknown }> = ({ metadata }) => {
  const flag = importIncompleteFromMetadata(metadata);
  if (!flag) return null;
  const when = flag.at ? ` (${flag.at.replace('T', ' ').slice(0, 16)} UTC)` : '';
  const what = flag.failedDatasets.length
    ? `A family-package import${when} failed for ${joinWithAnd(flag.failedDatasets)}${
        flag.importedDatasets.length ? `; ${joinWithAnd(flag.importedDatasets)} did import` : ''
      }.`
    : `A family-package import${when} did not complete, and which datasets it left out was not recorded.`;
  return (
    <div className="import-incomplete-banner" role="alert">
      <strong>Import incomplete.</strong> {what} Data from what failed is missing here, so the
      results and the report may be incomplete.{' '}
      {flag.jobId ? (
        <>
          Each dataset&rsquo;s error is recorded in import job <code>{flag.jobId}</code>.{' '}
        </>
      ) : null}
      Re-run the import to complete it. Sign-out needs this acknowledged with a reason.
    </div>
  );
};

export default ImportIncompleteBanner;
