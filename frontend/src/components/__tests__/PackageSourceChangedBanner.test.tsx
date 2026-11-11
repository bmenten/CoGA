import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import PackageSourceChangedBanner, {
  describePackageSourceChange,
} from '../PackageSourceChangedBanner';

// The family record's package_source_change, as the API serves it (synthetic values).
const CHANGED = {
  delivery_id: 'd1',
  folder: 'FAM1',
  imported_fingerprint: 'a'.repeat(64),
  kind: 'changed' as const,
  fingerprint: 'b'.repeat(64),
  listed: true,
  added: ['qc/report.html', 'qc/extra.txt'],
  removed: ['coverage/PROBAND.bw'],
  changed: ['alignments/PROBAND.cram'],
  added_count: 3,
  removed_count: 1,
  changed_count: 1,
  detected_at: '2026-11-11T10:00:00+00:00',
  found_at_sign_out: false,
  archived: null,
};

describe('PackageSourceChangedBanner', () => {
  it('shows nothing while the folder is as it was imported', () => {
    const { container } = render(<PackageSourceChangedBanner change={null} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('says what changed in the folder, names its paths, and what ends it', () => {
    render(<PackageSourceChangedBanner change={CHANGED} />);

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent(
      'Package folder changed after import. The package folder this family was imported from (FAM1) has changed: 3 files added, 1 removed and 1 changed in size or time since its import.',
    );
    expect(banner).toHaveTextContent('read files (IGV), signal tracks and QC report');
    expect(banner).toHaveTextContent(
      'Import the folder’s new delivery with overwrite, or refuse it with a reason, in Deliveries: an update skips the datasets the family holds, so this notice stays.',
    );
    expect(banner).toHaveTextContent('Added: qc/report.html, qc/extra.txt, and 1 more');
    expect(banner).toHaveTextContent('Removed: coverage/PROBAND.bw');
    expect(banner).toHaveTextContent('Changed in size or time: alignments/PROBAND.cram');
    expect(banner).toHaveTextContent('signing out asks for a reason');
  });

  it('says the source files are no longer available when the folder is gone', () => {
    render(
      <PackageSourceChangedBanner
        change={{ ...CHANGED, kind: 'gone', listed: false, added: [], removed: [], changed: [] }}
      />,
    );

    const banner = screen.getByRole('alert');
    expect(banner).toHaveTextContent('Package folder gone.');
    expect(banner).toHaveTextContent(
      'the source files CoGA showed from it (the read files in IGV, the signal tracks and the QC report) are no longer available',
    );
    expect(banner).not.toHaveTextContent('Added:');
    expect(banner).toHaveTextContent('if it was archived on purpose, have a superuser confirm it as archived in Deliveries');
  });

  it('says a gone folder confirmed as archived holds source files no longer available, without a warning', () => {
    render(
      <PackageSourceChangedBanner
        change={{
          ...CHANGED,
          kind: 'gone',
          listed: false,
          added: [],
          removed: [],
          changed: [],
          archived: {
            archived_at: '2026-11-11T11:00:00+00:00',
            archived_by: 'superuser',
            reason: 'Archived to tape after the report.',
          },
        }}
      />,
    );

    expect(screen.queryByRole('alert')).toBeNull();
    const notice = screen.getByRole('status');
    expect(notice).toHaveTextContent('Source files archived.');
    expect(notice).toHaveTextContent(
      'was confirmed as archived by superuser on 2026-11-11 11:00 UTC. Reason: Archived to tape after the report.',
    );
    expect(notice).toHaveTextContent(
      'source files (the read files in IGV, the signal tracks and the QC report) are archived and no longer available',
    );
    expect(notice).not.toHaveTextContent('signing out asks for a reason');
  });

  it('says a folder of more files than CoGA lists changed without naming them', () => {
    expect(describePackageSourceChange({ ...CHANGED, listed: false, added: [], added_count: null })).toBe(
      'changed, in more files than CoGA lists',
    );
    expect(describePackageSourceChange({ ...CHANGED, added_count: 1, removed_count: 0, changed_count: 0 })).toBe(
      'changed: 1 file added',
    );
  });
});
