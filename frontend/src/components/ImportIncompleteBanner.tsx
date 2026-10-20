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

/**
 * A package import that began writing the family and has not finished, or the mark of a
 * single-file upload or of a deletion that did not finish (its key says which:
 * {@link unfinishedWriter}).
 */
export interface FamilyImportUnfinished {
  /**
   * Its key in `import_unfinished`: the import job's id, or one of its own run outside a job;
   * `upload-…` or `deletion-…` for a mark.
   */
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
 * What wrote an `import_unfinished` entry, read from its key, as the server reads it
 * (family_package_registration.unfinished_entry_writer): a single-file upload marks the
 * family before its first ClickHouse write (`upload-…`), and so does a deletion of the
 * family's data there (`deletion-…`: an admin's deletion of a sample, of its structural
 * variants or of the family, a PED upload or manual family that replaces the family, or a
 * structure change that clears its data); any other key is a package import's (its job, or
 * a run of its own). The signed record keeps the key with the entry, so it says the same.
 */
export type UnfinishedWriter = 'import' | 'upload' | 'deletion';

export const unfinishedWriter = (key: string): UnfinishedWriter =>
  key.startsWith('upload-') ? 'upload' : key.startsWith('deletion-') ? 'deletion' : 'import';

/** A single-file upload's or a deletion's mark, as a sentence starts it. */
export const WRITER_LABELS: Record<Exclude<UnfinishedWriter, 'import'>, string> = {
  upload: 'A single-file upload',
  deletion: 'A deletion of this family’s data',
};

/**
 * The family's `import_unfinished` entries, oldest first; empty when there are none.
 *
 * A package import records its entry before it writes anything of the family and removes it
 * when it ends (family_package_registration.ImportMark). So an entry is an import still
 * running, or one whose process stopped part-way (a restart, a crash, running out of memory)
 * and left what it had not finished partly written. Only an import that imports that again
 * with overwrite removes it. A single-file upload, and a deletion of the family's data, mark
 * the family the same way before their first ClickHouse write (FamilyWriteMark); each item of
 * a mark goes once an upload or an import with overwrite has brought it again. As on the
 * server, a value that is set but not in the shape an import writes still counts, with
 * nothing to name.
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

/**
 * The keys of the `import_unfinished` entries that a failed overwrite's restore left: the
 * restore rewrites the family's rows whichever import or upload wrote them, and it did not
 * finish, so the entry lists every dataset of the family (`whole_family`), each until an
 * import, or a single-file upload, has brought it again with overwrite
 * (family_package_registration.whole_family_entry, record_family_upload). Only the family's
 * own metadata says so: the signed record and the sign-out refusal freeze the entry's
 * datasets alone.
 */
export const wholeFamilyImportKeys = (metadata: unknown): Set<string> => {
  if (!metadata || typeof metadata !== 'object') return new Set();
  const map = (metadata as Record<string, unknown>).import_unfinished;
  if (!map || typeof map !== 'object' || Array.isArray(map)) return new Set();
  return new Set(
    Object.entries(map as Record<string, unknown>)
      // An upload's or a deletion's mark lists its items the same way, but is no restore.
      .filter(([key]) => unfinishedWriter(key) === 'import')
      .filter(([, value]) => value && typeof value === 'object' && 'whole_family' in value)
      .map(([key]) => key),
  );
};

const bannerTime = (iso: string): string => `${iso.replace('T', ' ').slice(0, 16)} UTC`;

/** One unfinished import, as the banner says it. */
const UnfinishedImport: React.FC<{ entry: FamilyImportUnfinished; wholeFamily: boolean }> = ({
  entry,
  wholeFamily,
}) => {
  const pending = pendingDatasets(entry);
  const writer = unfinishedWriter(entry.key);
  if (writer !== 'import') {
    // A single-file upload (SAFE-28), and a deletion of the family's data (SAFE-31), mark the
    // family before their first ClickHouse write. No import job.
    const when = entry.at ? ` (${bannerTime(entry.at)})` : '';
    return (
      <>
        {WRITER_LABELS[writer]}
        {when}
        {writer === 'upload'
          ? ' began rewriting this family’s data and has not finished: it is still running, or it stopped or failed part-way.'
          : ' began and has not finished: it is still running, or it stopped or failed part-way.'}
        {pending.length ? ` ${joinWithAnd(pending)} may be partly written or missing.` : ''}{' '}
      </>
    );
  }
  const who = (
    <>
      A family-package import{entry.at ? ` (${bannerTime(entry.at)})` : ''}
      {entry.jobId ? (
        <>
          , import job <code>{entry.jobId}</code>,
        </>
      ) : null}
    </>
  );
  if (wholeFamily) {
    // Its restore rewrites the family's rows table by table, whichever import or upload wrote
    // them, and did not finish: not an import that is running, or one that stopped in a dataset.
    return (
      <>
        {who} failed, and putting this family&rsquo;s data back as it was did not finish:
        {pending.length
          ? ` ${joinWithAnd(pending)} may be partly written or missing, whichever import or upload wrote ${
              pending.length === 1 ? 'it' : 'them'
            }.`
          : ' any of its datasets may be partly written or missing.'}{' '}
      </>
    );
  }
  return (
    <>
      {who} began writing this family&rsquo;s data and has not finished: it is still running, or
      it stopped part-way.
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
 * or it stopped part-way), naming what it had not finished; after a failed overwrite whose
 * restore did not finish, naming every dataset of the family still to import again in full;
 * and while a single-file upload, or a deletion of the family's data, has not finished,
 * naming what it may have left partly written or missing.
 * Printed with the report, like the assembly-scope label. Sign-out refuses such a family
 * unless the signer acknowledges it with a reason, whatever this banner does.
 */
const ImportIncompleteBanner: React.FC<{ metadata?: unknown }> = ({ metadata }) => {
  const flag = importIncompleteFromMetadata(metadata);
  const unfinished = importUnfinishedFromMetadata(metadata);
  const wholeFamily = wholeFamilyImportKeys(metadata);
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
        <UnfinishedImport key={entry.key} entry={entry} wholeFamily={wholeFamily.has(entry.key)} />
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
      {wholeFamily.size
        ? 'Import this family again in full, with overwrite: every dataset named here, not only those that failed, and upload again what a single-file upload brought. An update keeps the data already there. '
        : unfinished.some((entry) => unfinishedWriter(entry.key) === 'import')
          ? 'Import what failed or did not finish again to complete it, with overwrite for what an import did not finish: an update keeps the data already there. '
          : flag
            ? 'Import what failed again to complete it. '
            : null}
      {unfinished.some((entry) => unfinishedWriter(entry.key) !== 'import')
        ? 'Upload or import again, with overwrite, what a single-file upload or a deletion did not finish: one without overwrite keeps the data already there. '
        : null}
      Sign-out needs this acknowledged with a reason.
    </div>
  );
};

export default ImportIncompleteBanner;
