import React from 'react';

import type { PackageSourceChangeOut } from '../lib/apiSchema.generated';
import { joinWithAnd } from '../lib/format';

/** A change after its import of a delivery's package folder, as the API serves it. */
export type PackageSourceChange = Pick<
  PackageSourceChangeOut,
  'kind' | 'listed' | 'added' | 'removed' | 'changed' | 'added_count' | 'removed_count' | 'changed_count'
> & { folder?: string | null };

const counted = (count: number | null | undefined, words: string): string | null =>
  typeof count === 'number' && count > 0 ? `${count} ${words}` : null;

/**
 * What changed of the folder, in a few words: "1 file added, 2 changed in size or time"; for a
 * folder whose files CoGA did not list (more than it lists), that it changed; for one gone, that
 * it is gone or cannot be read.
 */
export const describePackageSourceChange = (change: PackageSourceChange): string => {
  if (change.kind === 'gone') return 'gone, or cannot be read';
  if (!change.listed) return 'changed, in more files than CoGA lists';
  const parts = [
    counted(change.added_count, change.added_count === 1 ? 'file added' : 'files added'),
    counted(change.removed_count, 'removed'),
    counted(change.changed_count, 'changed in size or time'),
  ].filter((part): part is string => part !== null);
  return parts.length ? `changed: ${joinWithAnd(parts)}` : 'changed';
};

/** The paths a change names, by kind, each list as the watcher recorded it (at most 50). */
export const PackageSourceChangePaths: React.FC<{ change: PackageSourceChange }> = ({ change }) => {
  const groups: [string, string[], number | null | undefined][] = [
    ['Added', change.added ?? [], change.added_count],
    ['Removed', change.removed ?? [], change.removed_count],
    ['Changed in size or time', change.changed ?? [], change.changed_count],
  ];
  const named = groups.filter(([, paths]) => paths.length);
  if (!named.length) return null;
  return (
    <ul>
      {named.map(([label, paths, count]) => (
        <li key={label}>
          {label}:{' '}
          {paths.map((path, index) => (
            <React.Fragment key={path}>
              {index ? ', ' : ''}
              <code>{path}</code>
            </React.Fragment>
          ))}
          {typeof count === 'number' && count > paths.length ? `, and ${count - paths.length} more` : ''}
        </li>
      ))}
    </ul>
  );
};

/**
 * "Package folder changed after import" — shown on every family page while the package folder
 * the family's data was imported from has changed, or is gone, since its import (the watcher
 * follows it: package_changed_after_import.py). CoGA serves the family's read files (IGV),
 * signal tracks and QC report from that folder, so they may no longer be those imported. It
 * ends only when the folder is as imported again, or its new delivery is imported or refused
 * with a reason; sign-out warns of it, to acknowledge with a reason, whatever this banner does.
 */
const PackageSourceChangedBanner: React.FC<{
  change?: (PackageSourceChange & { folder: string }) | null;
}> = ({ change }) => {
  if (!change) return null;
  const folder = change.folder ? (
    <>
      {' '}
      (<code>{change.folder}</code>)
    </>
  ) : null;
  if (change.kind === 'gone') {
    return (
      <div className="import-incomplete-banner" role="alert">
        <strong>Package folder gone.</strong> The package folder this family was imported from{folder} is
        gone, or cannot be read, since its import: the source files CoGA showed from it (the read files in
        IGV, the signal tracks and the QC report) are no longer available. Put the folder back as it was
        imported. Until then, signing out asks for a reason.
      </div>
    );
  }
  return (
    <div className="import-incomplete-banner" role="alert">
      <strong>Package folder changed after import.</strong> The package folder this family was imported
      from{folder} has {describePackageSourceChange(change)} since its import. CoGA shows the family&rsquo;s
      read files (IGV), signal tracks and QC report from that folder, so they may no longer be the ones
      imported. Import the folder&rsquo;s new delivery (update or overwrite), or refuse it with a reason,
      in Deliveries. Until then, signing out asks for a reason.
      <PackageSourceChangePaths change={change} />
    </div>
  );
};

export default PackageSourceChangedBanner;
