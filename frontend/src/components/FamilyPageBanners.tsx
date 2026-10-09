import React from 'react';
import AssemblyScopeBanner from './AssemblyScopeBanner';
import ImportIncompleteBanner from './ImportIncompleteBanner';

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
 * validated scope (or one that could not be loaded), and a package import of the family that
 * partly failed or has not finished. Each shows only when it applies, and both print with a
 * report.
 *
 * `FamilyPageHeader` draws them; a family page with a header of its own (the NIPT pages, the
 * viewers) draws them through this, from the same family record, so that no family page shows
 * the family's data without them. Sign-out gates on the same conditions, whatever these show.
 */
const FamilyPageBanners: React.FC<{
  /** The family record's metadata, where an import records what it left incomplete. */
  metadata?: unknown;
  assemblyScope?: FamilyAssemblyScope;
}> = ({ metadata, assemblyScope }) => (
  <>
    <AssemblyScopeBanner
      assemblyName={assemblyScope?.name}
      assemblyValidated={assemblyScope?.validated}
      unavailable={assemblyScope?.unavailable}
      onRetry={assemblyScope?.onRetry}
    />
    <ImportIncompleteBanner metadata={metadata} />
  </>
);

export default FamilyPageBanners;
