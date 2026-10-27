// "Import incomplete" warning: a family whose package import partly failed keeps the
// datasets that did import, flagged `import_incomplete` in its metadata; an import that
// has begun writing the family and not finished (running, or stopped part-way) has its
// entry in `import_unfinished`.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ImportIncompleteBanner, {
  failuresByJob,
  importIncompleteFromMetadata,
  importUnfinishedFromMetadata,
  pendingDatasets,
  unfinishedWriter,
  wholeFamilyImportKeys,
} from '../ImportIncompleteBanner';

// As family_package_registration._flag_family_import_incomplete records it.
const FLAG = {
  at: '2026-09-12T10:14:00.123456+00:00',
  failed_datasets: ['snv', 'sv'],
  imported_datasets: ['coverage'],
  job_id: '3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b',
};
// As family_package_registration.ImportMark records it: stopped inside haplotypes.
const STOPPED_JOB = '7a1d2c3b-4e5f-4a6b-8c7d-9e0f1a2b3c4d';
const UNFINISHED = {
  [STOPPED_JOB]: {
    job_id: STOPPED_JOB,
    at: '2026-10-02T09:12:00.5+00:00',
    datasets: ['apcad', 'haplotypes', 'qdnaseq'],
    finished_datasets: ['apcad', 'qdnaseq'],
  },
};
// A flag written before the import job was recorded: the datasets only.
const OLD_FLAG = {
  at: FLAG.at,
  failed_datasets: FLAG.failed_datasets,
  imported_datasets: FLAG.imported_datasets,
};
// As family_package_registration records them after an overwrite that failed and whose
// restore did not finish: the flag names the import's datasets as failed, and its entry
// stays, naming every dataset of the family (the SV calls came with another import).
const RESTORE_JOB = '5b2e3c4d-6f7a-4b8c-9d0e-1f2a3b4c5d6e';
const RESTORE_FLAG = {
  at: '2026-10-13T09:12:00+00:00',
  failed_datasets: ['coverage', 'snv'],
  imported_datasets: [],
  job_id: RESTORE_JOB,
};
const SV_CALLS = { dataset: 'sv_needlr', scope: { source: null, samples: null } };
const WHOLE_FAMILY = {
  [RESTORE_JOB]: {
    job_id: RESTORE_JOB,
    at: '2026-10-13T09:00:00+00:00',
    datasets: ['coverage', 'snv', 'sv_needlr'],
    finished_datasets: [],
    whole_family: [
      { dataset: 'coverage', scope: { source: null, samples: ['S1'] } },
      { dataset: 'snv', scope: { source: 'auto', samples: null } },
      SV_CALLS,
    ],
  },
};

// As family_package_registration.FamilyWriteMark records them (SAFE-28): a structural-variant
// upload's mark, written before its rewrite, naming each sample whose Sniffles calls the
// rewrite deletes; and a deletion's, written before its first ClickHouse write (SAFE-31).
const UPLOAD_KEY = 'upload-0a1b2c3d4e5f60718293a4b5c6d7e8f9';
const UPLOAD_MARK = {
  [UPLOAD_KEY]: {
    job_id: null,
    at: '2026-10-14T08:30:00+00:00',
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
    // Written before the deletion's first ClickHouse write (SAFE-31): one that is running,
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

  it('names no job for a flag written before the job was recorded', () => {
    render(<ImportIncompleteBanner metadata={{ import_incomplete: OLD_FLAG }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/failed for snv and sv; coverage did import\./);
    expect(banner).not.toHaveTextContent(/import job/);
  });

  it('still warns when the flag records nothing to name', () => {
    render(<ImportIncompleteBanner metadata={{ import_incomplete: true }} />);

    expect(screen.getByRole('alert')).toHaveTextContent(
      /A family-package import did not complete, and which datasets it left out was not recorded\./,
    );
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

  it('never names a failure no job records under another failure’s job (SAFE-18)', () => {
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
    // brought is uploaded again (SAFE-19).
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

  it('still warns when an unfinished import records nothing to name', () => {
    render(<ImportIncompleteBanner metadata={{ import_unfinished: 'garbage' }} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      /A family-package import began writing this family.s data and has not finished/,
    );
    expect(banner).not.toHaveTextContent(/import job/);
  });

  it('shows nothing for a complete import', () => {
    const { container, rerender } = render(
      <ImportIncompleteBanner metadata={{ analysis_type: 'monogenic_nipt' }} />,
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
    const later = { job_id: 'job-2', at: '2026-10-02T11:00:00+00:00', datasets: ['snv'], finished_datasets: [] };
    expect(importUnfinishedFromMetadata({ import_unfinished: { 'job-2': later, ...UNFINISHED } })).toEqual([
      {
        key: STOPPED_JOB,
        jobId: STOPPED_JOB,
        at: UNFINISHED[STOPPED_JOB].at,
        datasets: ['apcad', 'haplotypes', 'qdnaseq'],
        finishedDatasets: ['apcad', 'qdnaseq'],
        wholeFamily: false,
      },
      { key: 'job-2', jobId: 'job-2', at: later.at, datasets: ['snv'], finishedDatasets: [], wholeFamily: false },
    ]);
  });

  it('takes a value of another shape as unfinished, with nothing to name', () => {
    // As sign-out does: a set value is never read as "nothing unfinished".
    const unknown = { jobId: null, at: null, datasets: [], finishedDatasets: [], wholeFamily: false };
    expect(importUnfinishedFromMetadata({ import_unfinished: 'garbage' })).toEqual([{ key: 'unreadable', ...unknown }]);
    expect(importUnfinishedFromMetadata({ import_unfinished: ['x'] })).toEqual([{ key: 'unreadable', ...unknown }]);
    expect(importUnfinishedFromMetadata({ import_unfinished: { k: 'not an entry' } })).toEqual([{ key: 'k', ...unknown }]);
    expect(
      importUnfinishedFromMetadata({ import_unfinished: { k: { job_id: 7, datasets: 'snv' } } }),
    ).toEqual([{ key: 'k', ...unknown }]);
  });

  it('reads none when none is recorded', () => {
    expect(importUnfinishedFromMetadata({})).toEqual([]);
    expect(importUnfinishedFromMetadata(null)).toEqual([]);
    expect(importUnfinishedFromMetadata({ import_unfinished: null })).toEqual([]);
    expect(importUnfinishedFromMetadata({ import_unfinished: false })).toEqual([]);
  });

  it('names as pending what the import had not finished', () => {
    const [entry] = importUnfinishedFromMetadata({ import_unfinished: UNFINISHED });
    expect(pendingDatasets(entry)).toEqual(['haplotypes']);
  });

  it('reads the entry a restore that did not finish left as one, every dataset pending', () => {
    // Its datasets, none finished, and marked as the restore's: the signed record and the
    // sign-out refusal freeze it with `whole_family: true` (SAFE-18).
    const [entry] = importUnfinishedFromMetadata({ import_unfinished: WHOLE_FAMILY });
    expect(entry).toEqual({
      key: RESTORE_JOB,
      jobId: RESTORE_JOB,
      at: WHOLE_FAMILY[RESTORE_JOB].at,
      datasets: ['coverage', 'snv', 'sv_needlr'],
      finishedDatasets: [],
      wholeFamily: true,
    });
    expect(pendingDatasets(entry)).toEqual(['coverage', 'snv', 'sv_needlr']);
    const frozen = { ...WHOLE_FAMILY[RESTORE_JOB], whole_family: true };
    expect(importUnfinishedFromMetadata({ import_unfinished: { [RESTORE_JOB]: frozen } })[0].wholeFamily).toBe(true);
    // An upload's mark lists its items the same way, but is no restore.
    expect(
      importUnfinishedFromMetadata({ import_unfinished: { [UPLOAD_KEY]: frozen } })[0].wholeFamily,
    ).toBe(false);
  });
});

describe('unfinishedWriter', () => {
  it('reads what wrote an entry from its key, as the server does', () => {
    expect(unfinishedWriter(UPLOAD_KEY)).toBe('upload');
    expect(unfinishedWriter(DELETION_KEY)).toBe('deletion');
    expect(unfinishedWriter(STOPPED_JOB)).toBe('import');
    expect(unfinishedWriter('run-5f2c')).toBe('import');
    expect(unfinishedWriter('unreadable')).toBe('import');
  });
});

describe('wholeFamilyImportKeys', () => {
  it('reads which entries a restore that did not finish left', () => {
    expect(wholeFamilyImportKeys({ import_unfinished: { ...UNFINISHED, ...WHOLE_FAMILY } })).toEqual(
      new Set([RESTORE_JOB]),
    );
    // An upload's or a deletion's mark lists its items the same way, but is no restore.
    expect(
      wholeFamilyImportKeys({ import_unfinished: { ...WHOLE_FAMILY, ...UPLOAD_MARK, ...DELETION_MARK } }),
    ).toEqual(new Set([RESTORE_JOB]));
    expect(wholeFamilyImportKeys({ import_unfinished: UNFINISHED })).toEqual(new Set());
    expect(wholeFamilyImportKeys({ import_unfinished: 'garbage' })).toEqual(new Set());
    expect(wholeFamilyImportKeys({ import_unfinished: { k: 'not an entry' } })).toEqual(new Set());
    expect(wholeFamilyImportKeys(null)).toEqual(new Set());
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
    });
    expect(importIncompleteFromMetadata({ import_incomplete: OLD_FLAG })?.jobId).toBeNull();
    expect(
      importIncompleteFromMetadata({ import_incomplete: { ...FLAG, job_id: 7 } })?.jobId,
    ).toBeNull();
  });

  it('takes a flag of another shape as incomplete, with nothing to name', () => {
    // As sign-out does: a set flag is never read as a complete import.
    const unknown = {
      at: null,
      failedDatasets: [],
      importedDatasets: [],
      jobId: null,
      failedJobs: {},
      withoutJob: [],
    };
    expect(importIncompleteFromMetadata({ import_incomplete: 'yes' })).toEqual(unknown);
    expect(importIncompleteFromMetadata({ import_incomplete: {} })).toEqual(unknown);
    expect(
      importIncompleteFromMetadata({ import_incomplete: { failed_datasets: 'snv' } }),
    ).toEqual(unknown);
  });

  it('reads a dataset the flag lists with no job as failed outside one (SAFE-18)', () => {
    // An upload of sv failed (no job), then an import failed for snv: the flag's job is the
    // import's. Before: sv was named under it.
    const flag = { ...FLAG, failed_jobs: { snv: FLAG.job_id, sv: null } };
    const read = importIncompleteFromMetadata({ import_incomplete: flag });
    expect(read?.failedJobs).toEqual({ snv: FLAG.job_id });
    expect(read?.withoutJob).toEqual(['sv']);
    expect(failuresByJob(read!)).toEqual([[FLAG.job_id, ['snv']]]);
    // No dataset with a job (a flag written before jobs were recorded, as a signed record
    // freezes it): none is named as failed outside one.
    const none = importIncompleteFromMetadata({
      import_incomplete: { ...OLD_FLAG, failed_jobs: { snv: null, sv: null } },
    });
    expect(none?.withoutJob).toEqual([]);
    expect(failuresByJob(none!)).toBeNull();
  });

  it('reads no flag as a complete import', () => {
    expect(importIncompleteFromMetadata({})).toBeNull();
    expect(importIncompleteFromMetadata(null)).toBeNull();
    expect(importIncompleteFromMetadata('not metadata')).toBeNull();
    expect(importIncompleteFromMetadata({ import_incomplete: false })).toBeNull();
  });
});
