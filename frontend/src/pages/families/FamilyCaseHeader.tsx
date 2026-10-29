import React, { useMemo } from 'react';
import type { FamilyAssemblyScope } from '../../components/FamilyPageBanners';
import { useProjectCatalog } from '../../lib/reference';
import FamilyPageHeader, { type FamilyPageHeaderFamily } from './FamilyPageHeader';
import FamilyCaseControls, { FamilyCaseActions, type FamilyCaseActionKey } from './FamilyCaseControls';
import FamilyModuleNav, { type FamilyModuleKey } from './FamilyModuleNav';
import { useFamilyDataPresence } from './familyDataPresence';
import { MONOGENIC_NIPT_ANALYSIS_TYPE } from './niptClassification';

/**
 * The top card of every family page (the workspace, the analyses, the Sample QC, the report),
 * one and the same: the family's facts on one compact line (members, assembly, projects, then the
 * page's own), the case's workflow fields, and at its foot one row with the family's analyses at
 * the left (a page's genome views among them) and its Sample QC and report at the right, the page it is on marked. From any of them
 * an analyst moves to another analysis or to the report without going back to the workspace.
 */
const FamilyCaseHeader: React.FC<{
  kicker: string;
  familyId: string;
  family: (FamilyPageHeaderFamily & { projects?: readonly string[] | null }) | null | undefined;
  projectId?: string | null;
  /** The family's assembly as the page names it ("GRCh38 p14", "Not linked"). */
  assemblyLabel: string;
  assemblyScope?: FamilyAssemblyScope;
  /** The page this is, marked in the foot row; none on the workspace. */
  current?: FamilyModuleKey | FamilyCaseActionKey;
  isWorkspace?: boolean;
  /** The page's own facts, after the family's: `.family-workspace-stat` items. */
  facts?: React.ReactNode;
  /** Page-specific content under the facts and the case's fields. */
  children?: React.ReactNode;
  actions?: React.ReactNode;
  /** In the row of analyses, after mtDNA analysis: a page's views of the genome (Genome,
   *  Circos), in their own colour. */
  views?: React.ReactNode;
  /** The NIPT report's query string, from the NIPT page (its panel and gene). */
  niptReportSearch?: string;
  /** Under the card's grid, across the card: a page's filter form. */
  footer?: React.ReactNode;
  /** Extra classes on the card, for a page that styles its own top card. */
  className?: string;
  phenotypeSampleIds?: string[];
}> = ({
  kicker,
  familyId,
  family,
  projectId,
  assemblyLabel,
  assemblyScope,
  current,
  isWorkspace = false,
  facts,
  children,
  actions,
  views,
  niptReportSearch,
  footer,
  className,
  phenotypeSampleIds,
}) => {
  const presence = useFamilyDataPresence({
    familyId,
    projectId,
    ready: Boolean(family) && (!family?.projects?.length || Boolean(projectId)),
  });
  const { data: catalog = [] } = useProjectCatalog();
  const projectNames = useMemo(() => {
    const names = new Map(catalog.map((project) => [project.id, project.name]));
    return (family?.projects ?? []).map((id) => ({ id, name: names.get(id) ?? id }));
  }, [catalog, family?.projects]);
  const isMonogenicNipt =
    (family?.metadata as { analysis_type?: string } | null | undefined)?.analysis_type ===
    MONOGENIC_NIPT_ANALYSIS_TYPE;

  return (
    <FamilyPageHeader
      kicker={kicker}
      familyId={familyId}
      family={family}
      projectId={projectId}
      isWorkspace={isWorkspace}
      assemblyScope={assemblyScope}
      actions={actions}
      className={`family-case-header${className ? ` ${className}` : ''}`}
      phenotypeSampleIds={phenotypeSampleIds}
      footer={
        <>
          {/* Across the card, under the pedigree too: the analyses at the left, the family's
              Sample QC and report at the right, at one height and in one style. Not printed. */}
          <div className="family-case-header-bar no-print">
            <FamilyModuleNav
              familyId={familyId}
              projectId={projectId}
              presence={presence}
              isMonogenicNipt={isMonogenicNipt}
              current={current}
              views={views}
            />
            <FamilyCaseActions
              familyId={familyId}
              projectId={projectId}
              presence={presence}
              current={current}
              isMonogenicNipt={isMonogenicNipt}
              niptReportSearch={niptReportSearch}
            />
          </div>
          {footer}
        </>
      }
    >
      <div className="family-workspace-summary">
        <div className="family-workspace-stat">
          <span className="stat-label">Members</span>
          <strong className="family-workspace-stat-value">{family?.members?.length ?? 0}</strong>
        </div>
        <div className="family-workspace-stat">
          <span className="stat-label">Assembly</span>
          <strong className="family-workspace-stat-copy">{assemblyLabel}</strong>
        </div>
        <div className="family-workspace-stat">
          <span className="stat-label">Projects</span>
          {projectNames.length ? (
            <span className="family-workspace-projects">
              {projectNames.map((project) => (
                <span key={project.id} className="table-chip">
                  {project.name}
                </span>
              ))}
            </span>
          ) : (
            <strong className="family-workspace-stat-copy">Not linked</strong>
          )}
        </div>
        {facts}
      </div>
      <div className="no-print">
        <FamilyCaseControls familyId={familyId} />
      </div>
      {children}
    </FamilyPageHeader>
  );
};

/**
 * One of a page's facts in the header's line (a count, an estimate): its label, then its value,
 * as the family's members, assembly and projects are given.
 */
export const FamilyFact: React.FC<{
  label: React.ReactNode;
  value?: React.ReactNode;
  title?: string;
  role?: string;
}> = ({ label, value, title, role }) => (
  <div className="family-workspace-stat" title={title} role={role}>
    <span className="stat-label">{label}</span>
    {value !== undefined ? (
      <>
        {' '}
        <strong className="family-workspace-stat-value">{value}</strong>
      </>
    ) : null}
  </div>
);

export default FamilyCaseHeader;
