import React from 'react';
import { Link } from 'react-router';
import type { NiptTargetCoverageOut } from '../../lib/apiSchema.generated';
import { depth } from './niptClassification';

interface NiptTargetCoverageProps {
  coverage: NiptTargetCoverageOut;
  // A panel or gene list is selected: the genes are those, weak or not.
  scoped: boolean;
  /** The coverage page, over the same genes. */
  detailsHref: string;
  /** Router state the coverage page takes its way back from: this page with its filters. */
  detailsState?: unknown;
}

/**
 * The plasma's per-target coverage (the capture panel's exons) on the NIPT page: the depth
 * over the targets in scope, and how many genes have a weak target -- a mean below the
 * critical depth, or a base without coverage -- where a fetal variant could be missed. The
 * genes themselves, below and above the target with each of their targets, are on the
 * coverage page the link opens (REQ-NIPT-006).
 */
const NiptTargetCoverage: React.FC<NiptTargetCoverageProps> = ({
  coverage,
  scoped,
  detailsHref,
  detailsState,
}) => {
  const weakGenes = coverage.genes.filter((gene) => gene.weak_targets > 0 || gene.targets === 0);
  const notTargets = weakGenes.filter((gene) => gene.targets === 0).length;
  return (
    <>
      <div className="family-workspace-summary">
        <div className="family-workspace-stat">
          <span className="family-workspace-stat-value">{depth(coverage.median_mean)}</span>
          <span className="family-workspace-stat-copy">
            Median target depth ({coverage.targets.toLocaleString()} target{coverage.targets === 1 ? '' : 's'}
            {scoped ? ' of the selected genes' : ''})
          </span>
        </div>
        <div className="family-workspace-stat">
          <span className="family-workspace-stat-value">{coverage.below_critical.toLocaleString()}</span>
          <span className="family-workspace-stat-copy">below {depth(coverage.critical_mean_depth)}</span>
        </div>
        <div className="family-workspace-stat">
          <span className="family-workspace-stat-value">{coverage.incomplete.toLocaleString()}</span>
          <span className="family-workspace-stat-copy">with uncovered bases</span>
        </div>
      </div>
      {weakGenes.length ? (
        <p className="status-note status-note--warning nipt-coverage-verdict" role="status">
          {weakGenes.length.toLocaleString()} gene{weakGenes.length === 1 ? '' : 's'} with a weak target (mean
          below {depth(coverage.critical_mean_depth)} or a base without coverage): a fetal variant there can be
          missed.
          {notTargets
            ? ` ${notTargets.toLocaleString()} of them ${notTargets === 1 ? 'is' : 'are'} not a target of the panel.`
            : ''}{' '}
          <Link to={detailsHref} state={detailsState}>
            Coverage details
          </Link>
        </p>
      ) : (
        <p className="table-subtle nipt-coverage-verdict">
          Every target {scoped ? 'of the selected genes ' : ''}reaches {depth(coverage.critical_mean_depth)} and is
          covered over its whole length.{' '}
          <Link to={detailsHref} state={detailsState}>
            Coverage details
          </Link>
        </p>
      )}
    </>
  );
};

export default NiptTargetCoverage;
