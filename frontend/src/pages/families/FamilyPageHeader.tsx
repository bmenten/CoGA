import React from 'react';
import { Link } from 'react-router';
import Pedigree from '../../components/visualizations/Pedigree';

/**
 * The top card every family-scoped page opens with.
 *
 * Each analysis page had grown its own version of this: some titled the family plainly
 * and carried a separate "Family" button that went where the title names, some linked
 * the title instead, and the pedigree panel picked up a different class depending on the
 * page. They are one thing seen from different pages, so they are drawn from one place.
 *
 * The title is the way back to the workspace — a page does not also need a button to the
 * same destination — except on the workspace itself, where `isWorkspace` leaves it as
 * plain text rather than a link to the page you are already on.
 */
export interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

export const parsePedigree = (pedigree?: string | null): PedRow[] => {
  if (!pedigree) return [];
  return pedigree
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const [fid, iid, pid, mid, sex, phen] = line.trim().split(/\s+/);
      return { fid, iid, pid, mid, sex, phen };
    });
};

/**
 * Only what the header needs. Each page fetches its own family shape — the members carry
 * page-specific extras — so this stays deliberately loose rather than forcing every
 * caller onto one record type.
 */
export interface FamilyPageHeaderFamily {
  family_id?: string;
  pedigree?: string | null;
  members?: readonly unknown[];
  relationships?: readonly unknown[];
  metadata?: Record<string, unknown> | null | unknown;
}

const FamilyPageHeader: React.FC<{
  /** Small caps label above the title — what this page is. */
  kicker: string;
  familyId?: string;
  family?: FamilyPageHeaderFamily | null;
  /** The workspace itself: the title stays plain rather than linking to this page. */
  isWorkspace?: boolean;
  projectId?: string | null;
  /** Extra classes on the section, for pages that style their own top card. */
  className?: string;
  /** Actions on the right of the header row. */
  actions?: React.ReactNode;
  /** Page-specific summary content under the title. */
  children?: React.ReactNode;
  /** Rendered inside the card, below the grid (filter bars and the like). */
  footer?: React.ReactNode;
  /** Samples whose phenotype ring the pedigree should draw (workspace only). */
  phenotypeSampleIds?: string[];
}> = ({
  kicker,
  familyId,
  family,
  isWorkspace = false,
  projectId,
  className,
  actions,
  children,
  footer,
  phenotypeSampleIds,
}) => {
  const label = familyId || family?.family_id || '';
  const pedRows = parsePedigree(family?.pedigree);
  const hasPedigree = pedRows.length > 0;
  const workspaceHref = projectId
    ? `/families/${label}?project_id=${encodeURIComponent(projectId)}`
    : `/families/${label}`;
  const title = `Family ${label}`;

  return (
    <section className={`surface-card page-top-card${className ? ` ${className}` : ''}`}>
      <div className={`page-top-card-grid${hasPedigree ? ' page-top-card-grid--with-visual' : ''}`}>
        <div className="page-top-card-copy">
          <div className="page-header">
            <div className="space-y-1">
              <p className="page-kicker">{kicker}</p>
              <h1 className="catalog-card-title">
                {isWorkspace || !label ? (
                  title
                ) : (
                  <Link to={workspaceHref} className="page-title-link">
                    {title}
                  </Link>
                )}
              </h1>
            </div>
            {actions ? <div className="inline-actions">{actions}</div> : null}
          </div>
          {/* A sibling of the header row, not a child of it: above 768px `.page-header`
              is a flex row, so anything nested inside it shrinks to its own content and
              a `repeat(auto-fit, …)` stat grid collapses to a single column. */}
          {children ? <div className="page-top-card-body">{children}</div> : null}
        </div>
        {hasPedigree && (
          <div className="page-top-card-visual">
            <div className="page-top-card-pedigree">
              <p className="analysis-section-title">Pedigree</p>
              <Pedigree
                rows={pedRows}
                members={family?.members as never}
                relationships={family?.relationships as never}
                phenotypeSampleIds={phenotypeSampleIds}
                inheritanceModel={
                  (
                    (family?.metadata as Record<string, unknown> | undefined)?.pgt as
                      | { inheritance_model?: string }
                      | undefined
                  )?.inheritance_model
                }
              />
            </div>
          </div>
        )}
      </div>
      {footer}
    </section>
  );
};

export default FamilyPageHeader;
