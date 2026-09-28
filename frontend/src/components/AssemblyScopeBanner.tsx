import React from 'react';

/**
 * "Not validated for clinical use" — shown wherever a family on a reference assembly
 * outside the validated scope is analysed or reported (TF-06 H12, #515).
 *
 * The scope itself is decided server-side (`VALIDATED_ASSEMBLIES`, default GRCh38) and
 * arrives as `assembly_validated` on the project; sign-out refuses such a family whatever
 * this banner does. Nothing is shown while the scope is unknown, so a page still loading
 * its reference does not flash the warning.
 */
const AssemblyScopeBanner: React.FC<{
  assemblyName?: string;
  assemblyValidated?: boolean;
}> = ({ assemblyName, assemblyValidated }) => {
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
