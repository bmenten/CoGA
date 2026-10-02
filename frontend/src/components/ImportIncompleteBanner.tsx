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
  /**
   * The job whose record holds each failed dataset's error: a later failure keeps an
   * earlier import's failed datasets, so they can come from different jobs.
   */
  failedJobs: Record<string, string>;
}

/**
 * The jobs that hold the failed datasets' errors, each with its datasets, when they come
 * from more than one job; null when one job (or none) holds them all.
 */
export const failuresByJob = (flag: FamilyImportIncomplete): [string, string[]][] | null => {
  const byJob = new Map<string, string[]>();
  flag.failedDatasets.forEach((name) => {
    const job = flag.failedJobs[name];
    if (job) byJob.set(job, [...(byJob.get(job) ?? []), name]);
  });
  if (byJob.size < 2) return null;
  return [...byJob.entries()].sort((a, b) => a[1][0].localeCompare(b[1][0]));
};

const stringList = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((item) => typeof item === 'string') ? value : null;

/**
 * The family's `import_incomplete` flag, or null when its import completed.
 *
 * A family-package import that partly fails keeps the datasets that did import and stamps
 * `metadata.import_incomplete` (family_package_registration._flag_family_import_incomplete);
 * it goes once an import has imported each failed dataset again. As on the server, a flag that is set but
 * not in the shape the import writes still counts as incomplete, with nothing to name,
 * and a flag written before the import job was recorded has no job to point to.
 */
export const importIncompleteFromMetadata = (metadata: unknown): FamilyImportIncomplete | null => {
  if (!metadata || typeof metadata !== 'object') return null;
  const flag = (metadata as Record<string, unknown>).import_incomplete;
  if (flag === undefined || flag === null || flag === false) return null;
  const record =
    typeof flag === 'object' && !Array.isArray(flag) ? (flag as Record<string, unknown>) : {};
  const jobId = typeof record.job_id === 'string' && record.job_id ? record.job_id : null;
  const failedDatasets = stringList(record.failed_datasets) ?? [];
  const jobs =
    record.failed_jobs && typeof record.failed_jobs === 'object' && !Array.isArray(record.failed_jobs)
      ? (record.failed_jobs as Record<string, unknown>)
      : {};
  const failedJobs: Record<string, string> = {};
  failedDatasets.forEach((name) => {
    const job = jobs[name];
    const resolved = typeof job === 'string' && job ? job : jobId;
    if (resolved) failedJobs[name] = resolved;
  });
  return {
    at: typeof record.at === 'string' && record.at ? record.at : null,
    failedDatasets,
    importedDatasets: stringList(record.imported_datasets) ?? [],
    jobId,
    failedJobs,
  };
};

/** A package import that began writing the family and has not finished. */
export interface FamilyImportUnfinished {
  /** Its key in `import_unfinished`: the import job's id, or one of its own run outside a job. */
  key: string;
  /** The import job, or null when it ran outside one (or none is recorded). */
  jobId: string | null;
  /** When it began writing the family (ISO timestamp), or null when not recorded. */
  at: string | null;
  /** What it set out to import. */
  datasets: string[];
  /** Those it finished; the others may be partly written or missing. */
  finishedDatasets: string[];
}

/** What an unfinished import had not finished: it may be partly written or missing. */
export const pendingDatasets = (entry: FamilyImportUnfinished): string[] =>
  entry.datasets.filter((name) => !entry.finishedDatasets.includes(name));

/**
 * The family's `import_unfinished` entries, oldest first; empty when there are none.
 *
 * A package import records its entry before it writes anything of the family and removes it
 * when it ends (family_package_registration.ImportMark). So an entry is an import still
 * running, or one whose process stopped part-way (a restart, a crash, running out of memory)
 * and left what it had not finished partly written. Only an import that imports that again
 * with overwrite removes it. As on the server, a value that is set but not in the shape an
 * import writes still counts, with nothing to name.
 */
export const importUnfinishedFromMetadata = (metadata: unknown): FamilyImportUnfinished[] => {
  if (!metadata || typeof metadata !== 'object') return [];
  const map = (metadata as Record<string, unknown>).import_unfinished;
  if (map === undefined || map === null || map === false) return [];
  const entries: [string, unknown][] =
    typeof map === 'object' && !Array.isArray(map)
      ? Object.entries(map as Record<string, unknown>)
      : [['unreadable', map]];
  return entries
    .map(([key, value]) => {
      const record =
        value && typeof value === 'object' && !Array.isArray(value)
          ? (value as Record<string, unknown>)
          : {};
      return {
        key,
        jobId: typeof record.job_id === 'string' && record.job_id ? record.job_id : null,
        at: typeof record.at === 'string' && record.at ? record.at : null,
        datasets: stringList(record.datasets) ?? [],
        finishedDatasets: stringList(record.finished_datasets) ?? [],
      };
    })
    .sort((a, b) => (a.at ?? '').localeCompare(b.at ?? '') || a.key.localeCompare(b.key));
};

const bannerTime = (iso: string): string => `${iso.replace('T', ' ').slice(0, 16)} UTC`;

/** One unfinished import, as the banner says it. */
const UnfinishedImport: React.FC<{ entry: FamilyImportUnfinished }> = ({ entry }) => {
  const pending = pendingDatasets(entry);
  return (
    <>
      A family-package import{entry.at ? ` (${bannerTime(entry.at)})` : ''}
      {entry.jobId ? (
        <>
          , import job <code>{entry.jobId}</code>,
        </>
      ) : null}{' '}
      began writing this family&rsquo;s data and has not finished: it is still running, or it
      stopped part-way.
      {pending.length
        ? ` ${joinWithAnd(pending)} may be partly written or missing${
            entry.finishedDatasets.length ? `; ${joinWithAnd(entry.finishedDatasets)} had finished` : ''
          }.`
        : ''}{' '}
    </>
  );
};

/**
 * "Import incomplete" — shown on every family page while the family's data only partly
 * loaded, naming what failed, and while an import of it has not finished (it is running,
 * or it stopped part-way), naming what it had not finished. Printed with the report, like
 * the assembly-scope label. Sign-out refuses such a family unless the signer acknowledges
 * it with a reason, whatever this banner does.
 */
const ImportIncompleteBanner: React.FC<{ metadata?: unknown }> = ({ metadata }) => {
  const flag = importIncompleteFromMetadata(metadata);
  const unfinished = importUnfinishedFromMetadata(metadata);
  if (!flag && !unfinished.length) return null;
  const when = flag?.at ? ` (${bannerTime(flag.at)})` : '';
  const what = !flag
    ? null
    : flag.failedDatasets.length
      ? `A family-package import${when} failed for ${joinWithAnd(flag.failedDatasets)}${
          flag.importedDatasets.length ? `; ${joinWithAnd(flag.importedDatasets)} did import` : ''
        }.`
      : `A family-package import${when} did not complete, and which datasets it left out was not recorded.`;
  return (
    <div className="import-incomplete-banner" role="alert">
      <strong>Import incomplete.</strong> {what ? `${what} ` : null}
      {unfinished.map((entry) => (
        <UnfinishedImport key={entry.key} entry={entry} />
      ))}
      {unfinished.length
        ? 'Data from what failed or did not finish may be missing or partly written here, so the results and the report may be incomplete. '
        : 'Data from what failed is missing here, so the results and the report may be incomplete. '}
      {flag && failuresByJob(flag) ? (
        <>
          Each dataset&rsquo;s error is recorded in its import job:{' '}
          {failuresByJob(flag)!.map(([job, names], index) => (
            <React.Fragment key={job}>
              {index ? '; ' : ''}
              {joinWithAnd(names)} in import job <code>{job}</code>
            </React.Fragment>
          ))}
          .{' '}
        </>
      ) : flag?.jobId ? (
        <>
          Each dataset&rsquo;s error is recorded in import job <code>{flag.jobId}</code>.{' '}
        </>
      ) : null}
      {unfinished.length
        ? 'Import what failed or did not finish again to complete it, with overwrite for what an import did not finish: an update keeps the data already there. '
        : 'Import what failed again to complete it. '}
      Sign-out needs this acknowledged with a reason.
    </div>
  );
};

export default ImportIncompleteBanner;
