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
  /** The page this is: its link is marked; a page outside the row (the report) marks none. */
  current?: string;
  /** A page's views of the genome (Genome, Circos), after the analyses of the family's own
   *  calls (mtDNA analysis last), before the NIPT and the variant summary. */
  views?: React.ReactNode;
}> = ({ familyId, projectId, presence, isMonogenicNipt, current, views }) => {
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
  const viewsBefore = shown.findIndex((module) => module.key === 'nipt' || module.key === 'variant-summary');
  const viewsGroup = views ? (
    <span className="family-case-header-views" role="group" aria-label="Genome views">
      {views}
    </span>
  ) : null;
  return (
    <div className="family-module-nav-block">
      {shown.length || viewsGroup ? (
        <nav className="family-module-nav" aria-label="Variant analyses">
          {shown.map((module, index) => (
            <React.Fragment key={module.key}>
              {index === viewsBefore ? viewsGroup : null}
              <Link
                to={routePath`/families/${familyId}/${module.key}${raw(module.withProject ? projectSuffix : '')}`}
                className={`family-module-link hover:no-underline${
                  module.key === 'variant-summary' ? ' family-module-link--end' : ''
                }`}
                aria-current={module.key === current ? 'page' : undefined}
              >
                {module.label}
              </Link>
            </React.Fragment>
          ))}
          {viewsBefore === -1 ? viewsGroup : null}
        </nav>
      ) : null}
      {presence.failedChecks.length ? (
        <QueryFailure
          what={`whether this family has ${presence.failedChecks.join(', ')}`}
          onRetry={presence.retryFailedChecks}
          consequence="Their links are shown, so you can open them."
        />
      ) : null}
    </div>
  );
};

export default FamilyModuleNav;
