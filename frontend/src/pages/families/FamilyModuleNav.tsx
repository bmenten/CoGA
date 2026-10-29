import React from 'react';
import { Link } from 'react-router';
import QueryFailure from '../../components/QueryFailure';
import { raw, routePath } from '../../lib/routePath';
import type { FamilyDataPresence } from './familyDataPresence';

/** The family's analysis pages, each a key the page it is on passes as `current`. */
export type FamilyModuleKey =
  | 'small-variants'
  | 'structural-variants'
  | 'repeat-expansions'
  | 'paraphase'
  | 'mitochondrial-dna'
  | 'nipt'
  | 'variant-summary';

/**
 * The family's analyses as one row of equal, tab-like links, on the workspace and on every
 * analysis page, so an analyst moves from one to another without going back to the workspace.
 *
 * A link appears once its data is known to exist (a failed check shows it, #607); the page it
 * is on is always shown, marked as current. The variant summary comes last, at the row's right
 * end: it summarises the structural variants (every SV caller), so it belongs with the analyses,
 * not with the family-level Sample QC and report.
 */
const FamilyModuleNav: React.FC<{
  familyId: string;
  projectId?: string | null;
  presence: FamilyDataPresence;
  isMonogenicNipt: boolean;
  current?: FamilyModuleKey;
}> = ({ familyId, projectId, presence, isMonogenicNipt, current }) => {
  const projectSuffix = projectId ? `?project_id=${encodeURIComponent(projectId)}` : '';
  const modules: { key: FamilyModuleKey; label: string; available: boolean; withProject: boolean }[] = [
    { key: 'small-variants', label: 'Small variants', available: presence.hasSmallVariants, withProject: true },
    {
      key: 'structural-variants',
      label: 'Structural variants',
      available: presence.hasStructuralVariants,
      withProject: true,
    },
    { key: 'repeat-expansions', label: 'Repeat expansions', available: presence.hasRepeatExpansions, withProject: false },
    { key: 'paraphase', label: 'Paraphase', available: presence.hasParaphase, withProject: false },
    { key: 'mitochondrial-dna', label: 'mtDNA analysis', available: presence.hasMitoDna, withProject: true },
    { key: 'nipt', label: 'Monogenic NIPT', available: isMonogenicNipt, withProject: false },
    { key: 'variant-summary', label: 'Variant summary', available: presence.hasVariantSummary, withProject: false },
  ];
  const shown = modules.filter((module) => module.available || module.key === current);
  return (
    <>
      {shown.length ? (
        <nav className="family-module-nav" aria-label="Variant analyses">
          {shown.map((module) => (
            <Link
              key={module.key}
              to={routePath`/families/${familyId}/${module.key}${raw(module.withProject ? projectSuffix : '')}`}
              className={`family-module-link hover:no-underline${
                module.key === 'variant-summary' ? ' family-module-link--end' : ''
              }`}
              aria-current={module.key === current ? 'page' : undefined}
            >
              {module.label}
            </Link>
          ))}
        </nav>
      ) : null}
      {presence.failedChecks.length ? (
        <QueryFailure
          what={`whether this family has ${presence.failedChecks.join(', ')}`}
          onRetry={presence.retryFailedChecks}
          consequence="Their links are shown, so you can open them."
        />
      ) : null}
    </>
  );
};

export default FamilyModuleNav;
