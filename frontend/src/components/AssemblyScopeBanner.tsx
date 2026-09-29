import React from 'react';

/**
 * "Not validated for clinical use" — shown wherever a family on a reference assembly
 * outside the validated scope is analysed or reported (TF-06 H12, #515).
 *
 * The scope itself is decided server-side (`VALIDATED_ASSEMBLIES`, default GRCh38) and
 * arrives as `assembly_validated` on the project; sign-out refuses such a family whatever
 * this banner does. Nothing is shown while the scope is loading, so a page still loading
 * its reference does not flash the warning. When the project catalogue could not be
 * loaded the scope stays unknown, and the banner says it is unconfirmed instead of
 * showing nothing (#608).
 */
const AssemblyScopeBanner: React.FC<{
  assemblyName?: string;
  assemblyValidated?: boolean;
  /** The reference could not be loaded, so the scope is unknown. */
  unavailable?: boolean;
  onRetry?: () => void;
}> = ({ assemblyName, assemblyValidated, unavailable, onRetry }) => {
  if (unavailable) {
    return (
      <div className="assembly-scope-banner" role="alert">
        <strong>Validated scope not confirmed.</strong> The family&apos;s reference assembly could
        not be loaded, so it is not known whether it is inside the assemblies CoGA is validated
        on. Treat the results as unconfirmed until it loads.{' '}
        {onRetry ? (
          <button type="button" className="button-link" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>
    );
  }
  if (assemblyValidated !== false) return null;
  return (
    <div className="assembly-scope-banner" role="alert">
      <strong>Not validated for clinical use.</strong>{' '}
      {assemblyName
        ? `${assemblyName} is outside the reference assemblies CoGA is validated on.`
        : 'No reference assembly is linked to this family, so it cannot be confirmed to be inside the validated scope.'}{' '}
      Results are for research use only, and the report cannot be signed out.
    </div>
  );
};

export default AssemblyScopeBanner;
