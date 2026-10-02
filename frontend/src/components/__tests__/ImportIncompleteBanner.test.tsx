// "Import incomplete" warning: a family whose package import partly failed keeps the
// datasets that did import, flagged `import_incomplete` in its metadata; an import that
// has begun writing the family and not finished (running, or stopped part-way) has its
// entry in `import_unfinished`.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ImportIncompleteBanner, {
  importIncompleteFromMetadata,
  importUnfinishedFromMetadata,
  pendingDatasets,
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

describe('ImportIncompleteBanner', () => {
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

  it('names a failed import and an unfinished one together', () => {
    render(
      <ImportIncompleteBanner metadata={{ import_incomplete: FLAG, import_unfinished: UNFINISHED }} />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(/failed for snv and sv; coverage did import\./);
    expect(banner).toHaveTextContent(/haplotypes may be partly written or missing/);
    expect(banner).toHaveTextContent(/Each dataset.s error is recorded in import job 3f6c1a2e/);
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
      },
      { key: 'job-2', jobId: 'job-2', at: later.at, datasets: ['snv'], finishedDatasets: [] },
    ]);
  });

  it('takes a value of another shape as unfinished, with nothing to name', () => {
    // As sign-out does: a set value is never read as "nothing unfinished".
    const unknown = { jobId: null, at: null, datasets: [], finishedDatasets: [] };
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
});

describe('importIncompleteFromMetadata', () => {
  it('reads the flag the import writes', () => {
    expect(importIncompleteFromMetadata({ import_incomplete: FLAG })).toEqual({
      at: FLAG.at,
      failedDatasets: ['snv', 'sv'],
      importedDatasets: ['coverage'],
      jobId: FLAG.job_id,
    });
    expect(importIncompleteFromMetadata({ import_incomplete: OLD_FLAG })?.jobId).toBeNull();
    expect(
      importIncompleteFromMetadata({ import_incomplete: { ...FLAG, job_id: 7 } })?.jobId,
    ).toBeNull();
  });

  it('takes a flag of another shape as incomplete, with nothing to name', () => {
    // As sign-out does: a set flag is never read as a complete import.
    const unknown = { at: null, failedDatasets: [], importedDatasets: [], jobId: null };
    expect(importIncompleteFromMetadata({ import_incomplete: 'yes' })).toEqual(unknown);
    expect(importIncompleteFromMetadata({ import_incomplete: {} })).toEqual(unknown);
    expect(
      importIncompleteFromMetadata({ import_incomplete: { failed_datasets: 'snv' } }),
    ).toEqual(unknown);
  });

  it('reads no flag as a complete import', () => {
    expect(importIncompleteFromMetadata({})).toBeNull();
    expect(importIncompleteFromMetadata(null)).toBeNull();
    expect(importIncompleteFromMetadata('not metadata')).toBeNull();
    expect(importIncompleteFromMetadata({ import_incomplete: false })).toBeNull();
  });
});
