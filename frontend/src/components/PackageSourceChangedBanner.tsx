import React from 'react';

import type { PackageSourceArchivedOut, PackageSourceChangeOut } from '../lib/apiSchema.generated';
import { joinWithAnd } from '../lib/format';

/** A change of a package folder after its import, as the API serves it. */
export type PackageSourceChange = Pick<
  PackageSourceChangeOut,
  'kind' | 'listed' | 'added' | 'removed' | 'changed' | 'added_count' | 'removed_count' | 'changed_count'
> & {
  folder?: string | null;
  // A gone folder a superuser confirmed as deliberately gone: no sign-out is warned of it.
  archived?: PackageSourceArchivedOut | null;
  // Found by a sign-out's own check of the folder, before the import watcher.
  found_at_sign_out?: boolean;
};

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

/** Who confirmed a gone folder as archived, and when (the time CoGA keeps, in UTC): "by
 * superuser on 2026-11-11 11:00 UTC". */
export const describeArchiving = (archived: PackageSourceArchivedOut): string =>
  `by ${archived.archived_by || 'unknown'}${
    archived.archived_at ? ` on ${archived.archived_at.replace('T', ' ').slice(0, 16)} UTC` : ''
  }`;

/** Why a gone folder was confirmed as archived, as a sentence of its own. */
const ArchivingReason: React.FC<{ archived: PackageSourceArchivedOut }> = ({ archived }) =>
  archived.reason ? (
    <>
      {' '}
      Reason: {archived.reason}
    </>
  ) : null;

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
 * CoGA serves the family's source files from is no longer as its data was imported whole (as a
 * new family or with overwrite), or is gone (the watcher follows it:
 * package_changed_after_import.py). CoGA serves the family's read files (IGV), signal tracks and
 * QC report from that folder, so they may no longer be those imported. It ends only when the
 * folder is as so imported again, when the folder's new delivery is imported with overwrite (an
 * update keeps it) or refused with a reason; sign-out warns of it, to acknowledge with a reason,
 * whatever this banner does. A gone folder a superuser confirmed as archived is said too: its
 * source files are no longer available, and no sign-out is warned of it.
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
  if (change.kind === 'gone' && change.archived) {
    return (
      <div className="import-incomplete-banner" role="status">
        <strong>Source files archived.</strong> The package folder this family was imported from{folder} is
        gone since its import, and was confirmed as archived {describeArchiving(change.archived)}.
        <ArchivingReason archived={change.archived} /> The family&rsquo;s source files (the read files in IGV,
        the signal tracks and the QC report) are archived and no longer available; a signed record says so.
      </div>
    );
  }
  if (change.kind === 'gone') {
    return (
      <div className="import-incomplete-banner" role="alert">
        <strong>Package folder gone.</strong> The package folder this family was imported from{folder} is
        gone, or cannot be read, since its import: the source files CoGA showed from it (the read files in
        IGV, the signal tracks and the QC report) are no longer available. Put the folder back as it was
        imported, or, if it was archived on purpose, have a superuser confirm it as archived in Deliveries.
        Until then, signing out asks for a reason.
      </div>
    );
  }
  return (
    <div className="import-incomplete-banner" role="alert">
      <strong>Package folder changed after import.</strong> The package folder this family was imported
      from{folder} has {describePackageSourceChange(change)} since its import. CoGA shows the family&rsquo;s
      read files (IGV), signal tracks and QC report from that folder, so they may no longer be the ones
      imported. Import the folder&rsquo;s new delivery with overwrite, or refuse it with a reason, in
      Deliveries: an update skips the datasets the family holds, so this notice stays. Until then, signing
      out asks for a reason.
      <PackageSourceChangePaths change={change} />
    </div>
  );
};

export default PackageSourceChangedBanner;
