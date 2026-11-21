// "Import incomplete" warning: a family whose package import partly failed keeps the
// datasets that did import, flagged `import_incomplete` in its metadata; an import that
// has begun writing the family and not finished (running, or stopped part-way) has its
// entry in `import_unfinished`. Each mark is read only in the shape CoGA writes it.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ImportIncompleteBanner, {
  failedDatasetWords,
  failuresByJob,
  importedAnalysisWords,
  importIncompleteFromMetadata,
  importMarksFromMetadata,
  ImportMarkShapeError,
  importUnfinishedFromMetadata,
  pendingDatasets,
  unfinishedWriter,
} from '../ImportIncompleteBanner';

/** One failure of a flag as family_package_registration._flag_payload writes it. */
const failure = (
  scope: { source: string | null; samples: string[] | null },
  jobId: string | null,
  analysisType: string | null = 'unspecified',
) => ({ scope, job_id: jobId, analysis_type: analysisType, at: '2026-09-12T10:14:00.123456+00:00', imported_datasets: [] });

// As family_package_registration._flag_payload writes it: each failed dataset with the job
// that holds its error and the analyses its failures are of (the package named none), and
// each failure.
const FLAG_JOB = '3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b';
const FLAG = {
  at: '2026-09-12T10:14:00.123456+00:00',
  failed_datasets: ['snv', 'sv'],
  imported_datasets: ['coverage'],
  job_id: FLAG_JOB,
  failed_jobs: { snv: FLAG_JOB, sv: FLAG_JOB },
  failed_analysis_types: { snv: ['unspecified'], sv: ['unspecified'] },
  failed_scopes: {
    snv: [failure({ source: 'clair3', samples: null }, FLAG_JOB)],
    sv: [failure({ source: null, samples: null }, FLAG_JOB)],
  },
};
// As family_package_registration.ImportMark writes it: stopped inside haplotypes.
const STOPPED_JOB = '7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const UNFINISHED = {
  [STOPPED_JOB]: {
    job_id: STOPPED_JOB,
    at: '2026-10-02T09:12:00.5+00:00',
    analysis_type: 'unspecified',
    datasets: ['apcad', 'haplotypes', 'qdnaseq'],
    finished_datasets: ['apcad', 'qdnaseq'],
    scopes: {
      apcad: { source: null, samples: ['EMB1'] },
      haplotypes: { source: null, samples: null },
      qdnaseq: { source: null, samples: ['EMB1'] },
    },
  },
};
/** `record` without `key`. */
const without = (record: Record<string, unknown>, key: string): Record<string, unknown> =>
  Object.fromEntries(Object.entries(record).filter(([name]) => name !== key));
// Marks in shapes CoGA does not write, each read as such: never as no mark, nor as a mark
// naming what it does not say.
const OTHER_FLAGS: [string, unknown][] = [
  ['true', true],
  ['false', false],
  ['text', 'yes'],
  ['a list', ['snv']],
  ['an empty map', {}],
  ['a flag without its time', without(FLAG, 'at')],
  ['a flag with a time that is no text', { ...FLAG, at: null }],
  ['a job that is no text', { ...FLAG, job_id: 7 }],
  ['a flag without its job', without(FLAG, 'job_id')],
  ['a flag without failed_jobs', without(FLAG, 'failed_jobs')],
  ['datasets that are no list', { ...FLAG, failed_datasets: 'snv' }],
  ['an empty dataset name', { ...FLAG, imported_datasets: [''] }],
  ['failed_jobs missing a failed dataset', { ...FLAG, failed_jobs: { snv: FLAG_JOB } }],
  ['failed_jobs naming a dataset that did not fail', { ...FLAG, failed_jobs: { ...FLAG.failed_jobs, coverage: FLAG_JOB } }],
  ['a failed dataset’s job that is no text', { ...FLAG, failed_jobs: { snv: FLAG_JOB, sv: 7 } }],
  ['a flag without the analyses of its failures', without(FLAG, 'failed_analysis_types')],
  ['analyses missing a failed dataset', { ...FLAG, failed_analysis_types: { snv: ['unspecified'] } }],
  ['a failed dataset with no analysis listed', { ...FLAG, failed_analysis_types: { snv: ['unspecified'], sv: [] } }],
  ['an analysis that is no name', { ...FLAG, failed_analysis_types: { snv: ['unspecified'], sv: [''] } }],
];
const OTHER_UNFINISHED: [string, unknown][] = [
  ['text', 'garbage'],
  ['false', false],
  ['a list', ['x']],
  ['an entry that is no map', { k: 'not an entry' }],
  ['an entry without its time', { k: without(UNFINISHED[STOPPED_JOB], 'at') }],
  ['an entry with a time that is no text', { k: { ...UNFINISHED[STOPPED_JOB], at: null } }],
  ['an entry without its job', { k: without(UNFINISHED[STOPPED_JOB], 'job_id') }],
  ['an entry without its finished datasets', { k: without(UNFINISHED[STOPPED_JOB], 'finished_datasets') }],
  ['a job that is no text', { k: { ...UNFINISHED[STOPPED_JOB], job_id: 7 } }],
  ['datasets that are no list', { k: { ...UNFINISHED[STOPPED_JOB], datasets: 'snv' } }],
  ['finished datasets that are no list', { k: { ...UNFINISHED[STOPPED_JOB], finished_datasets: null } }],
  ['whole_family that is neither its items nor true', { k: { ...UNFINISHED[STOPPED_JOB], whole_family: 'yes' } }],
  ['an entry without the analysis it is of', { k: without(UNFINISHED[STOPPED_JOB], 'analysis_type') }],
  ['an analysis that is no name', { k: { ...UNFINISHED[STOPPED_JOB], analysis_type: '' } }],
];
// As family_package_registration records them after an overwrite that failed and whose
// restore did not finish: the flag names the import's datasets as failed, and its entry
// stays, naming every dataset of the family (the SV calls came with another import).
const RESTORE_JOB = '5b2e3c4d-6f7a-4b8c-9d0e-1f2a3b4c5d6e';
const RESTORE_FLAG = {
  at: '2026-10-13T09:12:00+00:00',
  failed_datasets: ['coverage', 'snv'],
  imported_datasets: [],
  job_id: RESTORE_JOB,
  failed_jobs: { coverage: RESTORE_JOB, snv: RESTORE_JOB },
  failed_analysis_types: { coverage: ['unspecified'], snv: ['unspecified'] },
  failed_scopes: {
    coverage: [failure({ source: null, samples: ['S1'] }, RESTORE_JOB)],
    snv: [failure({ source: 'auto', samples: null }, RESTORE_JOB)],
  },
};
const SV_CALLS = { dataset: 'sv_needlr', scope: { source: null, samples: null } };
const WHOLE_FAMILY = {
  [RESTORE_JOB]: {
    job_id: RESTORE_JOB,
    at: '2026-10-13T09:00:00+00:00',
    analysis_type: 'unspecified',
    datasets: ['coverage', 'snv', 'sv_needlr'],
    finished_datasets: [],
    whole_family: [
      { dataset: 'coverage', scope: { source: null, samples: ['S1'] } },
      { dataset: 'snv', scope: { source: 'auto', samples: null } },
      SV_CALLS,
    ],
  },
};

// As family_package_registration.FamilyWriteMark records them: a structural-variant
// upload's mark, written before its rewrite, naming each sample whose Sniffles calls the
// rewrite deletes; and a deletion's, written before its first ClickHouse write.
const UPLOAD_KEY = 'upload-0a1b2c3d4e5f60718293a4b5c6d7e8f9';
const UPLOAD_MARK = {
  [UPLOAD_KEY]: {
    job_id: null,
    at: '2026-10-14T08:30:00+00:00',
    analysis_type: null,
    datasets: ['sv_sniffles'],
    finished_datasets: [],
    whole_family: [
      { dataset: 'sv_sniffles', scope: { source: null, samples: ['S1'] } },
      { dataset: 'sv_sniffles', scope: { source: null, samples: ['S2'] } },
    ],
  },
};
const DELETION_KEY = 'deletion-9f8e7d6c5b4a39281706f5e4d3c2b1a0';
const DELETION_MARK = {
  [DELETION_KEY]: {
    job_id: null,
    at: '2026-10-14T09:00:00+00:00',
    analysis_type: null,
    datasets: ['cnv', 'sv_needlr'],
    finished_datasets: [],
    whole_family: [
      { dataset: 'cnv', scope: { source: null, samples: ['S1'] } },
      { dataset: 'sv_needlr', scope: { source: null, samples: null } },
    ],
  },
};

describe('ImportIncompleteBanner', () => {
  it('names a single-file upload that has not finished, and what it may have left missing', () => {
    render(<ImportIncompleteBanner metadata={{ import_unfinished: UPLOAD_MARK }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/^Import incomplete\./);
    expect(banner).toHaveTextContent(
      'A single-file upload (2026-10-14 08:30 UTC) began rewriting this family’s data and has not finished: it is still running, or it stopped or failed part-way. sv_sniffles may be partly written or missing.',
    );
    // Not an import, nor a restore of one: no import job, and no whole family to import again.
    expect(banner).not.toHaveTextContent(/family-package import/);
    expect(banner).not.toHaveTextContent(/import job/);
    expect(banner).not.toHaveTextContent(/Import this family again in full/);
    expect(banner).toHaveTextContent(
      'Upload or import again, with overwrite, what a single-file upload or a deletion did not finish: one without overwrite keeps the data already there.',
    );
    expect(banner).toHaveTextContent(/Sign-out needs this acknowledged with a reason/);
  });

  it('names a deletion of the family’s data that has not finished', () => {
    render(<ImportIncompleteBanner metadata={{ import_unfinished: DELETION_MARK }} />);

    const banner = screen.getByRole('alert');
    // Written before the deletion's first ClickHouse write: one that is running,
    // stopped or failed part-way; not only one whose rewrite could not put the SVs back.
    expect(banner).toHaveTextContent(
      'A deletion of this family’s data (2026-10-14 09:00 UTC) began and has not finished: it is still running, or it stopped or failed part-way. cnv and sv_needlr may be partly written or missing.',
    );
    expect(banner).not.toHaveTextContent(/import job/);
    expect(banner).toHaveTextContent(/Upload or import again, with overwrite, what a single-file upload or a deletion did not finish/);
  });

  it('names an upload that has not finished beside a failed import', () => {
    render(<ImportIncompleteBanner metadata={{ import_incomplete: FLAG, import_unfinished: UPLOAD_MARK }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/A family-package import \(2026-09-12 10:14 UTC\) failed for snv and sv/);
    expect(banner).toHaveTextContent(/A single-file upload \(2026-10-14 08:30 UTC\) began rewriting/);
    expect(banner).toHaveTextContent(/Import what failed again to complete it\. Upload or import again, with overwrite/);
  });

  it('names what the import left out, and when', () => {
    render(<ImportIncompleteBanner metadata={{ import_incomplete: FLAG }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/^Import incomplete\./);
    expect(banner).toHaveTextContent(
      /A family-package import \(2026-09-12 10:14 UTC\) failed for snv and sv; coverage did import\./,
    );
    expect(banner).toHaveTextContent(/results and the report may be incomplete/);
    // The import job's record holds each dataset's error.
    expect(banner).toHaveTextContent(
      /Each dataset.s error is recorded in import job 3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b\./,
    );
    expect(banner).toHaveTextContent(/Sign-out needs this acknowledged with a reason/);
  });

  it('names no job when every failure was outside one', () => {
    // Single-file uploads of snv and sv failed: they run in no import job.
    const uploads = {
      ...FLAG,
      job_id: null,
      failed_jobs: { snv: null, sv: null },
      failed_analysis_types: { snv: [null], sv: [null] },
      failed_scopes: {
        snv: [failure({ source: 'clair3', samples: null }, null, null)],
        sv: [failure({ source: null, samples: null }, null, null)],
      },
    };
    render(<ImportIncompleteBanner metadata={{ import_incomplete: uploads }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/failed for snv and sv; coverage did import\./);
    expect(banner).not.toHaveTextContent(/import job/);
  });

  const UNREADABLE_BANNER =
    'Import incomplete. This family’s import marks are not in the shape CoGA writes, so this page cannot say what failed or did not finish. Its data may be missing or partly written, so the results and the report may be incomplete. Signing out fails until the marks are put right: report this as a defect.';

  it.each([
    ...OTHER_FLAGS.map(([what, flag]): [string, unknown] => [`a flag: ${what}`, { import_incomplete: flag }]),
    ...OTHER_UNFINISHED.map(([what, map]): [string, unknown] => [
      `import_unfinished: ${what}`,
      { import_unfinished: map },
    ]),
    // One mark in another shape beside one as written: nothing is named from either.
    ['a flag as written beside an entry in another shape', { import_incomplete: FLAG, import_unfinished: { k: 7 } }],
  ])('says the marks are not in the shape CoGA writes, naming nothing, for %s', (_what, metadata) => {
    render(<ImportIncompleteBanner metadata={metadata} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(UNREADABLE_BANNER);
    expect(banner).not.toHaveTextContent(/failed for|import job|acknowledged with a reason/);
  });

  it('names an import that has not finished, and what it had not finished', () => {
    render(<ImportIncompleteBanner metadata={{ import_unfinished: UNFINISHED }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/^Import incomplete\./);
    expect(banner).toHaveTextContent(
      `A family-package import (2026-10-02 09:12 UTC), import job ${STOPPED_JOB}, began writing this family’s data and has not finished: it is still running, or it stopped part-way. haplotypes may be partly written or missing; apcad and qdnaseq had finished.`,
    );
    expect(banner).toHaveTextContent(/may be missing or partly written here/);
    // An update would skip the half-written dataset.
    expect(banner).toHaveTextContent(/with overwrite for what an import did not finish/);
    expect(banner).toHaveTextContent(/Sign-out needs this acknowledged with a reason/);
  });

  it('names the job of each failed dataset when a later failure kept an earlier one', () => {
    render(
      <ImportIncompleteBanner
        metadata={{ import_incomplete: { ...FLAG, failed_jobs: { snv: 'job-earlier', sv: FLAG.job_id } } }}
      />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      `Each dataset’s error is recorded in its import job: snv in import job job-earlier; sv in import job ${FLAG.job_id}.`,
    );
    // Not the import as a whole: what failed is imported again.
    expect(banner).toHaveTextContent(/Import what failed again to complete it\./);
  });

  it('never names a failure no job records under another failure’s job', () => {
    render(
      <ImportIncompleteBanner
        metadata={{ import_incomplete: { ...FLAG, failed_jobs: { snv: FLAG.job_id, sv: null } } }}
      />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      `No import job records the error of sv, which failed in a single-file upload or in an import run outside a job. Each other dataset’s error is recorded in its import job: snv in import job ${FLAG.job_id}.`,
    );
    expect(banner).not.toHaveTextContent(/Each dataset’s error is recorded in import job/);
  });

  it('names a failed import and an unfinished one together', () => {
    render(
      <ImportIncompleteBanner metadata={{ import_incomplete: FLAG, import_unfinished: UNFINISHED }} />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/failed for snv and sv; coverage did import\./);
    expect(banner).toHaveTextContent(/haplotypes may be partly written or missing/);
    expect(banner).toHaveTextContent(/Each dataset.s error is recorded in import job 3f6c1a2e/);
  });

  it('asks for the whole family again after a restore that did not finish', () => {
    render(
      <ImportIncompleteBanner
        metadata={{ import_incomplete: RESTORE_FLAG, import_unfinished: WHOLE_FAMILY }}
      />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/failed for coverage and snv\./);
    expect(banner).toHaveTextContent(
      `A family-package import (2026-10-13 09:00 UTC), import job ${RESTORE_JOB}, failed, and putting this family’s data back as it was did not finish: coverage, snv and sv_needlr may be partly written or missing, whichever import or upload wrote them.`,
    );
    // Not an import that is running or stopped: one whose restore did not finish.
    expect(banner).not.toHaveTextContent(/still running/);
    // Importing again what failed (coverage, snv) does not complete it; what an upload
    // brought is uploaded again.
    expect(banner).toHaveTextContent(
      'Import this family again in full, with overwrite: every dataset named here, not only those that failed, and upload again what a single-file upload brought. An update keeps the data already there.',
    );
    expect(banner).not.toHaveTextContent(/Import what failed/);
    expect(banner).toHaveTextContent(/Sign-out needs this acknowledged with a reason/);
  });

  it('names what of the family is still to import again', () => {
    // Imported again with overwrite: coverage and snv, so the flag went; the SV calls not.
    const left = {
      [RESTORE_JOB]: { ...WHOLE_FAMILY[RESTORE_JOB], datasets: ['sv_needlr'], whole_family: [SV_CALLS] },
    };
    render(<ImportIncompleteBanner metadata={{ import_unfinished: left }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      /did not finish: sv_needlr may be partly written or missing, whichever import or upload wrote it\./,
    );
    expect(banner).toHaveTextContent(/Import this family again in full, with overwrite/);
  });

  it('shows nothing for a complete import', () => {
    const { container, rerender } = render(
      <ImportIncompleteBanner metadata={{ analyses: ['monogenic_nipt'] }} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(<ImportIncompleteBanner metadata={undefined} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<ImportIncompleteBanner metadata={{ import_incomplete: null }} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<ImportIncompleteBanner metadata={{ import_unfinished: {} }} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('importUnfinishedFromMetadata', () => {
  it('reads the entries the imports write, oldest first', () => {
    const later = {
      job_id: 'job-2',
      at: '2026-10-02T11:00:00+00:00',
      analysis_type: 'long_read_wgs',
      datasets: ['snv'],
      finished_datasets: [],
      scopes: { snv: { source: 'clair3', samples: null } },
    };
    expect(importUnfinishedFromMetadata({ import_unfinished: { 'job-2': later, ...UNFINISHED } })).toEqual([
      {
        key: STOPPED_JOB,
        jobId: STOPPED_JOB,
        at: UNFINISHED[STOPPED_JOB].at,
        analysisType: 'unspecified',
        datasets: ['apcad', 'haplotypes', 'qdnaseq'],
        finishedDatasets: ['apcad', 'qdnaseq'],
        wholeFamily: false,
      },
      {
        key: 'job-2',
        jobId: 'job-2',
        at: later.at,
        analysisType: 'long_read_wgs',
        datasets: ['snv'],
        finishedDatasets: [],
        wholeFamily: false,
      },
    ]);
  });

  it.each(OTHER_UNFINISHED)('throws for a map or an entry in another shape: %s', (_what, map) => {
    // Never read as "nothing unfinished", nor as an entry naming what it does not say.
    expect(() => importUnfinishedFromMetadata({ import_unfinished: map })).toThrow(ImportMarkShapeError);
    expect(() => importUnfinishedFromMetadata({ import_unfinished: map })).toThrow(
      /^The family’s import_unfinished (map|entry) is not in the shape CoGA writes\.$/,
    );
  });

  it('reads none when none is recorded', () => {
    expect(importUnfinishedFromMetadata({})).toEqual([]);
    expect(importUnfinishedFromMetadata(null)).toEqual([]);
    expect(importUnfinishedFromMetadata({ import_unfinished: null })).toEqual([]);
    expect(importUnfinishedFromMetadata({ import_unfinished: {} })).toEqual([]);
  });

  it('reads an upload’s and a deletion’s mark, and an entry as the sign-out freezes it', () => {
    expect(importUnfinishedFromMetadata({ import_unfinished: { ...UPLOAD_MARK, ...DELETION_MARK } })).toEqual([
      {
        key: UPLOAD_KEY,
        jobId: null,
        at: UPLOAD_MARK[UPLOAD_KEY].at,
        analysisType: null,
        datasets: ['sv_sniffles'],
        finishedDatasets: [],
        wholeFamily: false,
      },
      {
        key: DELETION_KEY,
        jobId: null,
        at: DELETION_MARK[DELETION_KEY].at,
        analysisType: null,
        datasets: ['cnv', 'sv_needlr'],
        finishedDatasets: [],
        wholeFamily: false,
      },
    ]);
    // Frozen into a refusal or a signed record: no scopes, no items.
    const frozen = without(UNFINISHED[STOPPED_JOB], 'scopes');
    expect(importUnfinishedFromMetadata({ import_unfinished: { [STOPPED_JOB]: frozen } })).toEqual(
      importUnfinishedFromMetadata({ import_unfinished: UNFINISHED }),
    );
  });

  it('names as pending what the import had not finished', () => {
    const [entry] = importUnfinishedFromMetadata({ import_unfinished: UNFINISHED });
    expect(pendingDatasets(entry)).toEqual(['haplotypes']);
  });

  it('reads the entry a restore that did not finish left as one, every dataset pending', () => {
    // Its datasets, none finished, and marked as the restore's: the signed record and the
    // sign-out refusal freeze it with `whole_family: true`.
    const [entry] = importUnfinishedFromMetadata({ import_unfinished: WHOLE_FAMILY });
    expect(entry).toEqual({
      key: RESTORE_JOB,
      jobId: RESTORE_JOB,
      at: WHOLE_FAMILY[RESTORE_JOB].at,
      analysisType: 'unspecified',
      datasets: ['coverage', 'snv', 'sv_needlr'],
      finishedDatasets: [],
      wholeFamily: true,
    });
    expect(pendingDatasets(entry)).toEqual(['coverage', 'snv', 'sv_needlr']);
    const frozen = { ...WHOLE_FAMILY[RESTORE_JOB], whole_family: true };
    expect(importUnfinishedFromMetadata({ import_unfinished: { [RESTORE_JOB]: frozen } })[0].wholeFamily).toBe(true);
    // An upload's or a deletion's mark lists its items the same way, but is no restore.
    expect(
      importUnfinishedFromMetadata({ import_unfinished: { [UPLOAD_KEY]: frozen } })[0].wholeFamily,
    ).toBe(false);
    const restores = (metadata: unknown) =>
      importUnfinishedFromMetadata(metadata)
        .filter((unfinished) => unfinished.wholeFamily)
        .map((unfinished) => unfinished.key);
    expect(restores({ import_unfinished: { ...UNFINISHED, ...WHOLE_FAMILY } })).toEqual([RESTORE_JOB]);
    expect(restores({ import_unfinished: { ...WHOLE_FAMILY, ...UPLOAD_MARK, ...DELETION_MARK } })).toEqual([
      RESTORE_JOB,
    ]);
  });
});

describe('unfinishedWriter', () => {
  it('reads what wrote an entry from its key, as the server does', () => {
    expect(unfinishedWriter(UPLOAD_KEY)).toBe('upload');
    expect(unfinishedWriter(DELETION_KEY)).toBe('deletion');
    expect(unfinishedWriter(STOPPED_JOB)).toBe('import');
    expect(unfinishedWriter('run-5f2c')).toBe('import');
  });
});

describe('importIncompleteFromMetadata', () => {
  it('reads the flag the import writes', () => {
    expect(importIncompleteFromMetadata({ import_incomplete: FLAG })).toEqual({
      at: FLAG.at,
      failedDatasets: ['snv', 'sv'],
      importedDatasets: ['coverage'],
      jobId: FLAG.job_id,
      // A flag naming one job: it holds every dataset's error.
      failedJobs: { snv: FLAG.job_id, sv: FLAG.job_id },
      withoutJob: [],
      failedAnalysisTypes: { snv: ['unspecified'], sv: ['unspecified'] },
    });
    // As the sign-out freezes it: without failed_scopes, read the same.
    const frozen = without(FLAG, 'failed_scopes');
    expect(importIncompleteFromMetadata({ import_incomplete: frozen })).toEqual(
      importIncompleteFromMetadata({ import_incomplete: FLAG }),
    );
  });

  it.each(OTHER_FLAGS)('throws for a flag in another shape: %s', (_what, flag) => {
    // Never read as a complete import, nor as a flag naming what it does not say.
    expect(() => importIncompleteFromMetadata({ import_incomplete: flag })).toThrow(ImportMarkShapeError);
    expect(() => importIncompleteFromMetadata({ import_incomplete: flag })).toThrow(
      /^The family’s import_incomplete flag is not in the shape CoGA writes\.$/,
    );
  });

  it('reads a dataset the flag lists with no job as failed outside one', () => {
    // An upload of sv failed (no job), then an import failed for snv: the flag's job is the
    // import's, and sv is not named under it.
    const flag = { ...FLAG, failed_jobs: { snv: FLAG.job_id, sv: null } };
    const read = importIncompleteFromMetadata({ import_incomplete: flag });
    expect(read?.failedJobs).toEqual({ snv: FLAG.job_id });
    expect(read?.withoutJob).toEqual(['sv']);
    expect(failuresByJob(read!)).toEqual([[FLAG.job_id, ['snv']]]);
    // No dataset with a job (each failed in a single-file upload): none is named as failed
    // outside one, as no job is named beside them.
    const none = importIncompleteFromMetadata({
      import_incomplete: {
        ...FLAG,
        job_id: null,
        failed_jobs: { snv: null, sv: null },
        failed_analysis_types: { snv: [null], sv: [null] },
        failed_scopes: {
          snv: [failure({ source: 'clair3', samples: null }, null, null)],
          sv: [failure({ source: null, samples: null }, null, null)],
        },
      },
    });
    expect(none?.withoutJob).toEqual([]);
    expect(failuresByJob(none!)).toBeNull();
  });

  it('reads no flag as a complete import', () => {
    expect(importIncompleteFromMetadata({})).toBeNull();
    expect(importIncompleteFromMetadata(null)).toBeNull();
    expect(importIncompleteFromMetadata('not metadata')).toBeNull();
    // As the sign-out freezes no flag.
    expect(importIncompleteFromMetadata({ import_incomplete: null })).toBeNull();
  });
});

describe('importMarksFromMetadata', () => {
  it('reads both marks as written', () => {
    expect(importMarksFromMetadata({ import_incomplete: FLAG, import_unfinished: UNFINISHED })).toEqual({
      incomplete: importIncompleteFromMetadata({ import_incomplete: FLAG }),
      unfinished: importUnfinishedFromMetadata({ import_unfinished: UNFINISHED }),
    });
    expect(importMarksFromMetadata({})).toEqual({ incomplete: null, unfinished: [] });
  });

  it('reads a family with either mark in another shape as unreadable', () => {
    expect(importMarksFromMetadata({ import_incomplete: true })).toBe('unreadable');
    expect(importMarksFromMetadata({ import_incomplete: false, import_unfinished: UNFINISHED })).toBe('unreadable');
    expect(importMarksFromMetadata({ import_incomplete: FLAG, import_unfinished: 'garbage' })).toBe('unreadable');
  });
});

// A family of several analyses (short-read and then long-read WGS of the same DNA) shares dataset
// types: each mark names the analysis it is of, whose sign-out it holds.
describe('the analysis each mark is of', () => {
  const SEVERAL = {
    analyses: ['long_read_wgs', 'short_read_wgs'],
    import_incomplete: {
      ...FLAG,
      failed_datasets: ['qc', 'snv'],
      failed_jobs: { qc: FLAG_JOB, snv: FLAG_JOB },
      // The long-read import failed for snv; qc failed in both analyses' imports and in an upload.
      failed_analysis_types: { qc: [null, 'long_read_wgs', 'short_read_wgs'], snv: ['long_read_wgs'] },
      failed_scopes: {
        qc: [
          failure({ source: null, samples: ['S1'] }, null, null),
          failure({ source: null, samples: ['S1'] }, FLAG_JOB, 'long_read_wgs'),
          failure({ source: null, samples: ['S1'] }, FLAG_JOB, 'short_read_wgs'),
        ],
        snv: [failure({ source: 'deepvariant', samples: null }, FLAG_JOB, 'long_read_wgs')],
      },
    },
    import_unfinished: { [STOPPED_JOB]: { ...UNFINISHED[STOPPED_JOB], analysis_type: 'short_read_wgs' } },
  };

  it('names the analysis of each failed dataset and unfinished import', () => {
    render(<ImportIncompleteBanner metadata={SEVERAL} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      /A family-package import \(2026-09-12 10:14 UTC\) failed for qc \(long-read WGS, short-read WGS, an upload\) and snv \(long-read WGS\); coverage did import\./,
    );
    expect(banner).toHaveTextContent(
      `A family-package import of the short-read WGS data (2026-10-02 09:12 UTC), import job ${STOPPED_JOB}, began writing`,
    );
    expect(banner).toHaveTextContent(
      'Sign-out needs this acknowledged with a reason. Each analysis’s sign-out is held only by what its own imports left and by what names no analysis; a restore that did not finish holds every analysis’s.',
    );
  });

  it('says nothing of analyses in a family of one', () => {
    render(<ImportIncompleteBanner metadata={{ ...SEVERAL, analyses: ['short_read_wgs'] }} />);
    expect(screen.getByRole('alert')).not.toHaveTextContent(/Each analysis’s sign-out/);
  });

  it('reads the analyses as written, and names those it has a name for', () => {
    const read = importIncompleteFromMetadata(SEVERAL)!;
    expect(read.failedAnalysisTypes).toEqual(SEVERAL.import_incomplete.failed_analysis_types);
    expect(failedDatasetWords(read, 'qc')).toBe('qc (long-read WGS, short-read WGS, an upload)');
    // A package that named no analysis, or an upload: no name.
    const plain = importIncompleteFromMetadata({ import_incomplete: FLAG })!;
    expect(failedDatasetWords(plain, 'snv')).toBe('snv');
    // An upload's failure beside an analysis's is named too.
    const mixed = { ...read, failedAnalysisTypes: { snv: [null, 'long_read_wgs'] } };
    expect(failedDatasetWords(mixed, 'snv')).toBe('snv (long-read WGS, an upload)');
    expect(importUnfinishedFromMetadata(SEVERAL)[0].analysisType).toBe('short_read_wgs');
    expect(importedAnalysisWords('long_read_wgs')).toBe(' of the long-read WGS data');
    expect(importedAnalysisWords('unspecified')).toBe('');
    expect(importedAnalysisWords(null)).toBe('');
  });
});
