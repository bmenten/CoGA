import React from 'react';
import { joinWithAnd } from '../lib/format';

/** What a partly failed family-package import left out, as the family's metadata records it. */
export interface FamilyImportIncomplete {
  /** When the import ran (ISO timestamp). */
  at: string;
  failedDatasets: string[];
  importedDatasets: string[];
  /** The import job whose record holds each dataset's error, or null for a failure outside one. */
  jobId: string | null;
  /**
   * The job whose record holds each failed dataset's error: a later failure keeps an
   * earlier import's failed datasets, so they can come from different jobs.
   */
  failedJobs: Record<string, string>;
  /**
   * The failed datasets no import job records an error of, beside others a job does: each
   * failed in a single-file upload, or an import run outside a job, which the flag lists
   * with no job. The flag's own job is another failure's. Empty when no dataset has a job.
   */
  withoutJob: string[];
}

/**
 * The jobs that hold the failed datasets' errors, each with its datasets, when they come
 * from more than one job, or when a failure no job records is beside them
 * ({@link FamilyImportIncomplete.withoutJob}); null when one job (or none) holds them all.
 */
export const failuresByJob = (flag: FamilyImportIncomplete): [string, string[]][] | null => {
  const byJob = new Map<string, string[]>();
  flag.failedDatasets.forEach((name) => {
    const job = flag.failedJobs[name];
    if (job) byJob.set(job, [...(byJob.get(job) ?? []), name]);
  });
  if (byJob.size < 2 && !(byJob.size && flag.withoutJob.length)) return null;
  return [...byJob.entries()].sort((a, b) => a[1][0].localeCompare(b[1][0]));
};

/**
 * A family's import mark (its `import_incomplete` flag, or its `import_unfinished` map or an
 * entry of it) in a shape CoGA does not write. The readers below throw it rather than read
 * the mark as no mark, or as naming what it does not say; {@link importMarksFromMetadata}
 * turns it into what a page shows instead. The message names the mark, never a value.
 */
export class ImportMarkShapeError extends Error {
  constructor(mark: string) {
    super(`The family’s ${mark} is not in the shape CoGA writes.`);
    this.name = 'ImportMarkShapeError';
  }
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** A list of names (datasets), as every mark writes them. */
const isNameList = (value: unknown): value is string[] =>
  Array.isArray(value) && value.every((name) => typeof name === 'string' && name !== '');

/** A job as a mark records it: its id, or null for a write outside a job. */
const isJob = (value: unknown): value is string | null => value === null || typeof value === 'string';

const sameNames = (a: string[], b: string[]): boolean => {
  const left = new Set(a);
  const right = new Set(b);
  return left.size === right.size && [...left].every((name) => right.has(name));
};

/**
 * The family's `import_incomplete` flag, or null when it has none (the key absent, or null as
 * a sign-out freezes no flag).
 *
 * A family-package import that partly fails keeps the datasets that did import and stamps
 * `metadata.import_incomplete` (family_package_registration._flag_payload); it goes once an
 * import has imported each failed dataset again. The sign-out's refusal and the signed record
 * freeze the same keys, less `failed_scopes`, and are read here too. This reads the keys the
 * pages show: when (`at`), the failed and the imported datasets, the flag's job, and the job of
 * each failed dataset (`failed_jobs`, one per failed dataset), each in the shape it is written.
 * It does not read `failed_scopes`. Throws {@link ImportMarkShapeError} for a flag in any other
 * shape.
 */
export const importIncompleteFromMetadata = (metadata: unknown): FamilyImportIncomplete | null => {
  if (!metadata || typeof metadata !== 'object') return null;
  const flag = (metadata as Record<string, unknown>).import_incomplete;
  if (flag === undefined || flag === null) return null;
  if (isRecord(flag)) {
    const { at, failed_datasets: failed, imported_datasets: imported, job_id: jobId, failed_jobs: jobs } = flag;
    if (
      typeof at === 'string' &&
      isNameList(failed) &&
      isNameList(imported) &&
      isJob(jobId) &&
      isRecord(jobs) &&
      sameNames(Object.keys(jobs), failed) &&
      Object.values(jobs).every(isJob)
    ) {
      const failedJobs: Record<string, string> = {};
      const listedWithoutJob: string[] = [];
      failed.forEach((name) => {
        const job = jobs[name] as string | null;
        // Listed with no job: it failed outside one, so the flag's job, another failure's,
        // does not hold its error.
        if (job === null) listedWithoutJob.push(name);
        else failedJobs[name] = job;
      });
      return {
        at,
        failedDatasets: failed,
        importedDatasets: imported,
        jobId,
        failedJobs,
        // Named only beside a failure a job records: with no job beside them, no job is named
        // that could be taken for theirs.
        withoutJob: Object.keys(failedJobs).length ? listedWithoutJob : [],
      };
    }
  }
  throw new ImportMarkShapeError('import_incomplete flag');
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
  /** The import job, or null when it ran outside one (a single-file upload's or a deletion's mark). */
  jobId: string | null;
  /** When it began writing the family (ISO timestamp). */
  at: string;
  /** What it set out to import. */
  datasets: string[];
  /** Those it finished; the others may be partly written or missing. */
  finishedDatasets: string[];
  /**
   * Left by a failed overwrite's restore that did not finish, its datasets the whole family's
   * (`whole_family`: a list in the family's metadata, `true` in a signed record or a sign-out
   * refusal). Never an upload's or a deletion's mark, which lists its items the same way.
   */
  wholeFamily: boolean;
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
 * a mark goes once an upload or an import with overwrite has brought it again.
 *
 * None when the map is absent or null. The sign-out's refusal and the signed record freeze each
 * entry's job, `at`, datasets and finished datasets, and `whole_family: true` for a restore's,
 * and are read here too. This reads those keys, each in the shape it is written, and
 * `whole_family` as the restore's list (or `true`, frozen); it does not read the entries'
 * scopes or items. Throws {@link ImportMarkShapeError} for a map or an entry in any other shape.
 */
export const importUnfinishedFromMetadata = (metadata: unknown): FamilyImportUnfinished[] => {
  if (!metadata || typeof metadata !== 'object') return [];
  const map = (metadata as Record<string, unknown>).import_unfinished;
  if (map === undefined || map === null) return [];
  if (!isRecord(map)) throw new ImportMarkShapeError('import_unfinished map');
  return Object.entries(map)
    .map(([key, entry]): FamilyImportUnfinished => {
      if (isRecord(entry)) {
        const { job_id: jobId, at, datasets, finished_datasets: finished, whole_family: whole } = entry;
        if (
          isJob(jobId) &&
          typeof at === 'string' &&
          isNameList(datasets) &&
          isNameList(finished) &&
          (whole === undefined || whole === true || Array.isArray(whole))
        ) {
          return {
            key,
            jobId,
            at,
            datasets,
            finishedDatasets: finished,
            // An upload's or a deletion's mark lists its items the same way, but is no restore.
            wholeFamily: unfinishedWriter(key) === 'import' && whole !== undefined,
          };
        }
      }
      throw new ImportMarkShapeError('import_unfinished entry');
    })
    .sort((a, b) => a.at.localeCompare(b.at) || a.key.localeCompare(b.key));
};

/** A family's two import marks, read. */
export interface FamilyImportMarks {
  /** Its `import_incomplete` flag, or null when it has none. */
  incomplete: FamilyImportIncomplete | null;
  /** Its `import_unfinished` entries, oldest first. */
  unfinished: FamilyImportUnfinished[];
}

/**
 * The family's import marks, or `unreadable` when one is not in the shape CoGA writes
 * ({@link ImportMarkShapeError}). Every page that shows them says so then, and names nothing:
 * the server's sign-out fails on such a mark, so its import gate stays closed.
 */
export const importMarksFromMetadata = (metadata: unknown): FamilyImportMarks | 'unreadable' => {
  try {
    return {
      incomplete: importIncompleteFromMetadata(metadata),
      unfinished: importUnfinishedFromMetadata(metadata),
    };
  } catch (error) {
    if (error instanceof ImportMarkShapeError) return 'unreadable';
    throw error;
  }
};

const bannerTime = (iso: string): string => `${iso.replace('T', ' ').slice(0, 16)} UTC`;

/** The banner's text for import marks not in the shape CoGA writes. */
const UNREADABLE_MARKS =
  'This family’s import marks are not in the shape CoGA writes, so this page cannot say what failed or did not finish. Its data may be missing or partly written, so the results and the report may be incomplete. Signing out fails until the marks are put right: report this as a defect.';

/** One unfinished import, as the banner says it. */
const UnfinishedImport: React.FC<{ entry: FamilyImportUnfinished }> = ({ entry }) => {
  const pending = pendingDatasets(entry);
  const writer = unfinishedWriter(entry.key);
  if (writer !== 'import') {
    // A single-file upload, and a deletion of the family's data, mark the
    // family before their first ClickHouse write. No import job.
    return (
      <>
        {WRITER_LABELS[writer]} ({bannerTime(entry.at)})
        {writer === 'upload'
          ? ' began rewriting this family’s data and has not finished: it is still running, or it stopped or failed part-way.'
          : ' began and has not finished: it is still running, or it stopped or failed part-way.'}
        {pending.length ? ` ${joinWithAnd(pending)} may be partly written or missing.` : ''}{' '}
      </>
    );
  }
  const who = (
    <>
      A family-package import ({bannerTime(entry.at)})
      {entry.jobId ? (
        <>
          , import job <code>{entry.jobId}</code>,
        </>
      ) : null}
    </>
  );
  if (entry.wholeFamily) {
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
 *
 * A mark not in the shape CoGA writes ({@link importMarksFromMetadata}) shows the same banner,
 * naming nothing: the family's data may be missing or partly written, and its sign-out fails
 * until the mark is put right.
 */
const ImportIncompleteBanner: React.FC<{ metadata?: unknown }> = ({ metadata }) => {
  const marks = importMarksFromMetadata(metadata);
  if (marks === 'unreadable') {
    return (
      <div className="import-incomplete-banner" role="alert">
        <strong>Import incomplete.</strong> {UNREADABLE_MARKS}
      </div>
    );
  }
  const { incomplete: flag, unfinished } = marks;
  if (!flag && !unfinished.length) return null;
  const when = flag ? ` (${bannerTime(flag.at)})` : '';
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
          {flag.withoutJob.length ? (
            <>
              No import job records the error of {joinWithAnd(flag.withoutJob)}, which failed in a
              single-file upload or in an import run outside a job. Each other dataset&rsquo;s error
              is recorded in its import job:{' '}
            </>
          ) : (
            <>Each dataset&rsquo;s error is recorded in its import job: </>
          )}
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
      {unfinished.some((entry) => entry.wholeFamily)
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
