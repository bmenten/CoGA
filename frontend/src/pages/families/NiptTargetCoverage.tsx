import React from 'react';
import type { NiptTargetCoverageOut, NiptTargetOut } from '../../lib/apiSchema.generated';
import { depth } from './niptClassification';

const targetLabel = (target: NiptTargetOut): string => {
  const parts = target.attribute.split(';');
  const exon = parts.length > 4 ? parts[parts.length - 1] : '';
  return exon ? `${target.gene} exon ${exon}` : target.gene;
};

const weakReason = (target: NiptTargetOut, critical: number): string => {
  if (target.mean == null || target.mean === 0) return 'no coverage';
  if (target.mean < critical) return `mean ${depth(target.mean)}`;
  return `${target.proportion_covered?.toFixed(1) ?? '—'}% covered`;
};

interface NiptTargetCoverageProps {
  coverage: NiptTargetCoverageOut;
  // A panel or gene list is selected: the genes are those, weak or not.
  scoped: boolean;
}

// Over the whole panel the weak genes run to hundreds: the weakest are shown.
const MAX_WEAK_GENE_CHIPS = 40;

/**
 * The plasma's per-target coverage (the capture panel's exons): the depth over the
 * targets in scope, and every gene with a weak target -- a mean below the critical depth,
 * or a base without coverage -- where a fetal variant could be missed.
 */
const NiptTargetCoverage: React.FC<NiptTargetCoverageProps> = ({ coverage, scoped }) => {
  const allWeakGenes = coverage.genes.filter((gene) => gene.weak_targets > 0 || gene.targets === 0);
  const weakGenes = allWeakGenes.slice(0, MAX_WEAK_GENE_CHIPS);
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
      {allWeakGenes.length ? (
        <div className="nipt-coverage-qc" role="status">
          <p className="table-subtle">
            {allWeakGenes.length.toLocaleString()} gene{allWeakGenes.length === 1 ? '' : 's'} with a weak target (mean
            below {depth(coverage.critical_mean_depth)} or a base without coverage): a fetal variant there can be
            missed.
            {allWeakGenes.length > weakGenes.length
              ? ` The ${weakGenes.length} weakest are shown; select a gene panel to see its genes.`
              : ''}
          </p>
          <div className="table-chip-list">
            {weakGenes.map((gene) =>
              gene.targets === 0 ? (
                <span key={gene.gene} className="table-chip table-chip--critical" title={`${gene.gene}: not in the panel`}>
                  {gene.gene} · not a target
                </span>
              ) : (
                <span
                  key={gene.gene}
                  className={`table-chip ${gene.min_mean == null || gene.min_mean === 0 ? 'table-chip--critical' : 'table-chip--warning'}`}
                  title={gene.weak
                    .map((target) => `${targetLabel(target)}: ${weakReason(target, coverage.critical_mean_depth)}`)
                    .join('\n')}
                >
                  {gene.gene} · {gene.weak_targets} of {gene.targets} targets weak
                </span>
              ),
            )}
          </div>
        </div>
      ) : (
        <p className="table-subtle">
          Every target {scoped ? 'of the selected genes ' : ''}reaches {depth(coverage.critical_mean_depth)} and is
          covered over its whole length.
        </p>
      )}
    </>
  );
};

export default NiptTargetCoverage;
