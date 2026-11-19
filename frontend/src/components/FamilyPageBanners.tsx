import React from 'react';
import AssemblyScopeBanner from './AssemblyScopeBanner';
import ImportIncompleteBanner from './ImportIncompleteBanner';
import IntakeCorrectionsNotice from './IntakeCorrectionsNotice';
import PackageSourceChangedBanner from './PackageSourceChangedBanner';
import type { FamilyPackageSourceChangeOut } from '../lib/apiSchema.generated';

/** The family's reference assembly and whether it is inside the validated scope. */
export interface FamilyAssemblyScope {
  name?: string;
  validated?: boolean;
  /** The reference could not be loaded, so the scope is unknown. */
  unavailable?: boolean;
  onRetry?: () => void;
}

/**
 * The warnings every family page carries in its header: a reference assembly outside the
 * validated scope (or one that could not be loaded), a package import of the family that
 * partly failed or has not finished, and each package folder the family came from that changed, or
 * went, after its import (the family record's `package_source_changes`: one per analysis it was
 * imported for, such as a monogenic and a conventional NIPT of one pregnancy). Each shows only when it
 * applies, and each prints with a report. Beneath them, a note when the delivered package the
 * family came from was corrected in CoGA before its import, listing each correction.
 *
 * `FamilyPageHeader` draws them; a family page with a header of its own (the NIPT pages, the
 * viewers) draws them through this, from the same family record, so that no family page shows
 * the family's data without them. Sign-out gates on the same conditions, whatever these show.
 */
const FamilyPageBanners: React.FC<{
  /** The family record's metadata, where an import records what it left incomplete. */
  metadata?: unknown;
  assemblyScope?: FamilyAssemblyScope;
  /** The family record's `package_source_changes`: each package folder that changed or went after import. */
  packageSourceChanges?: FamilyPackageSourceChangeOut[];
}> = ({ metadata, assemblyScope, packageSourceChanges }) => (
  <>
    <AssemblyScopeBanner
      assemblyName={assemblyScope?.name}
      assemblyValidated={assemblyScope?.validated}
      unavailable={assemblyScope?.unavailable}
      onRetry={assemblyScope?.onRetry}
    />
    <ImportIncompleteBanner metadata={metadata} />
    {(packageSourceChanges ?? []).map((change) => (
      <PackageSourceChangedBanner key={change.analysis_type} change={change} />
    ))}
    <IntakeCorrectionsNotice metadata={metadata} />
  </>
);

export default FamilyPageBanners;
