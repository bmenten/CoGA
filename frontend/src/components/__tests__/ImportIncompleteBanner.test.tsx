// "Import incomplete" warning: a family whose package import partly failed keeps the
// datasets that did import, flagged `import_incomplete` in its metadata.

import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import ImportIncompleteBanner, { importIncompleteFromMetadata } from '../ImportIncompleteBanner';

// As family_package_registration._flag_family_import_incomplete records it.
const FLAG = {
  at: '2026-09-12T10:14:00.123456+00:00',
  failed_datasets: ['snv', 'sv'],
  imported_datasets: ['coverage'],
  job_id: '3f6c1a2e-8b4d-4e5f-9a7b-1c2d3e4f5a6b',
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

  it('shows nothing for a complete import', () => {
    const { container, rerender } = render(
      <ImportIncompleteBanner metadata={{ analysis_type: 'monogenic_nipt' }} />,
    );
    expect(container).toBeEmptyDOMElement();

    rerender(<ImportIncompleteBanner metadata={undefined} />);
    expect(container).toBeEmptyDOMElement();

    rerender(<ImportIncompleteBanner metadata={{ import_incomplete: null }} />);
    expect(container).toBeEmptyDOMElement();
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
