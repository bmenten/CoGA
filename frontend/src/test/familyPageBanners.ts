import { screen } from '@testing-library/react';
import { expect } from 'vitest';

/**
 * A family's metadata after a package import that partly failed (`import_incomplete`), with
 * another import that began writing the family and has not finished (`import_unfinished`).
 * Every family page warns *Import incomplete* for either.
 */
export const INCOMPLETE_IMPORT_METADATA = {
  import_incomplete: {
    at: '2026-09-12T10:14:00+00:00',
    failed_datasets: ['sv'],
    imported_datasets: ['snv'],
    job_id: 'job-failed',
  },
  import_unfinished: {
    'job-stopped': {
      job_id: 'job-stopped',
      at: '2026-09-13T08:00:00+00:00',
      datasets: ['coverage'],
      finished_datasets: [],
    },
  },
};

/** A project (from `GET /projects`) on an assembly outside the validated scope. */
export const offScopeProject = (id: string) => ({
  _id: id,
  name: 'Research project',
  species_id: 'species-1',
  species_name: 'Homo sapiens',
  assembly_id: 'assembly-37',
  assembly_name: 'GRCh37',
  assembly_version: 'p13',
  assembly_validated: false,
  families: [],
  samples: [],
});

/**
 * The *Import incomplete* banner, once it shows: it must name both the import that failed
 * and the one that has not finished.
 */
export const findImportIncompleteBanner = async (): Promise<HTMLElement> => {
  const banner = (await screen.findByText('Import incomplete.')).closest<HTMLElement>('[role="alert"]');
  expect(banner).not.toBeNull();
  expect(banner).toHaveTextContent(/A family-package import \(2026-09-12 10:14 UTC\) failed for sv; snv did import\./);
  expect(banner).toHaveTextContent(/import job job-stopped, began writing this family’s data and has not finished/);
  expect(banner).toHaveTextContent(/coverage may be partly written or missing/);
  return banner as HTMLElement;
};

/** The *Not validated for clinical use* banner of a family on {@link offScopeProject}. */
export const findAssemblyScopeBanner = async (): Promise<HTMLElement> => {
  const banner = (await screen.findByText('Not validated for clinical use.')).closest<HTMLElement>(
    '[role="alert"]',
  );
  expect(banner).not.toBeNull();
  expect(banner).toHaveTextContent(/GRCh37 is outside the reference assemblies CoGA is validated on/);
  return banner as HTMLElement;
};
