import React from 'react';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';

/**
 * The anatomy every variant card shares, whatever kind of finding it holds (small variant,
 * repeat expansion, Paraphase region, mtDNA variant), so an analyst finds each part in the
 * same place on every page:
 *
 *   head     the gene (to its gene page) — its diseases, what the finding is; at the right
 *            its classification or status; under it the review's tag chips
 *   columns  "Variant" first (what and where it is, its annotation), "Genotypes" second (each
 *            member's call, FamilyGenotypeGrid), then what the kind of finding adds
 *   footer   the actions at the left, the links at the right in one order (FindingLinks)
 *
 * It renders the small-variant card's own classes, so the four read as one design.
 */

export type FindingRail = 'pathogenic' | 'benign' | 'warning' | 'neutral';

export interface FindingColumn {
  key: string;
  title: React.ReactNode;
  content: React.ReactNode;
}

const FindingCard: React.FC<{
  /** The heading row's left part: GeneDiseaseHeading. */
  heading: React.ReactNode;
  /** The heading row's right part: the classification or status chips. */
  tags?: React.ReactNode;
  /** Between the heading and the columns: the review's tag chips. */
  subhead?: React.ReactNode;
  /** Under the heading of an excluded finding: its ExcludedCardBar. */
  excludedBar?: React.ReactNode;
  /** An excluded finding not unfolded: the heading and its bar alone. */
  folded?: boolean;
  columns: FindingColumn[];
  /** A line under the columns, for what the whole finding needs said. */
  note?: React.ReactNode;
  actions?: React.ReactNode;
  links?: React.ReactNode;
  /** Below the footer: an expandable detail section. */
  more?: React.ReactNode;
  rail?: FindingRail;
  className?: string;
  'aria-label'?: string;
}> = ({
  heading,
  tags,
  subhead,
  excludedBar,
  folded = false,
  columns,
  note,
  actions,
  links,
  more,
  rail = 'neutral',
  className,
  ...rest
}) => (
  <article
    aria-label={rest['aria-label']}
    className={`variant-card finding-card${rail !== 'neutral' ? ` variant-card--clinvar-${rail}` : ''}${
      className ? ` ${className}` : ''
    }`}
  >
    <div className="variant-card-head">
      {heading}
      {tags ? <div className="variant-card-headtags">{tags}</div> : null}
    </div>
    {excludedBar}
    {folded ? null : (
      <>
        {subhead}
        <div className="variant-card-cols">
          {columns.map((column) => (
            <section key={column.key} className="variant-card-col">
              <p className="variant-card-col-title">{column.title}</p>
              {column.content}
            </section>
          ))}
        </div>
        {note}
        {actions || links ? (
          <div className="variant-card-footer">
            <div className="variant-card-quick">{actions}</div>
            {links}
          </div>
        ) : null}
        {more}
      </>
    )}
  </article>
);

export interface FindingGenomeLink {
  href: string;
  /** The accessible name: where the view opens. */
  label: string;
}

/**
 * A card's links, always in one order at the right of its footer: IGV, Chromosome view,
 * OMIM, then what the kind of finding adds (DECIPHER, MITOMAP). The genome views open a tab,
 * as they leave the list behind; OMIM and the others leave CoGA.
 */
export const FindingLinks: React.FC<{
  igv?: FindingGenomeLink | null;
  chromosome?: FindingGenomeLink | null;
  omim?: string | null;
  children?: React.ReactNode;
}> = ({ igv, chromosome, omim, children }) => (
  <div className="variant-card-nav">
    {igv ? (
      <GenomeWorkspaceLink
        to={igv.href}
        className="variant-card-resource variant-card-resource--clinical"
        label={igv.label}
      >
        IGV
      </GenomeWorkspaceLink>
    ) : null}
    {chromosome ? (
      <GenomeWorkspaceLink
        to={chromosome.href}
        className="variant-card-resource variant-card-resource--clinical"
        label={chromosome.label}
      >
        Chromosome view
      </GenomeWorkspaceLink>
    ) : null}
    {omim ? (
      <a
        href={omim}
        target="_blank"
        rel="noreferrer"
        className="variant-card-resource variant-card-resource--clinical"
      >
        OMIM
      </a>
    ) : null}
    {children}
  </div>
);

export default FindingCard;
