import React, { useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type { ApiNiptCoverageRegion, ApiNiptCoverageSummary, GenePanel } from '../../lib/apiTypes';
import type { NiptGeneTargetsOut, NiptTargetCoverageGeneOut } from '../../lib/apiSchema.generated';
import PageState from '../../components/PageState';
import QueryFailure from '../../components/QueryFailure';
import { depth, lowCoverageChipClass, lowCoverageDetail, targetExon } from './niptClassification';

// The gene query splits into terms as the backend splits it.
const GENE_TERM_SPLIT = /[\s,;]+/;
// A whole panel holds thousands of genes: the first are drawn, the rest on request.
const GENE_ROWS_SHOWN = 300;

const coveredPercent = (value?: number | null): string =>
  value == null ? '—' : `${value.toFixed(value >= 99.95 ? 0 : 1)}%`;

/** Why a target is weak, in the words of the NIPT page's count. */
const weakReason = (target: NiptGeneTargetsOut['targets'][number], critical: number): string => {
  if (target.mean == null || target.mean === 0) return 'no coverage';
  if (target.mean < critical) return `mean below ${depth(critical)}`;
  return `${coveredPercent(target.proportion_covered)} covered`;
};

/** One gene's targets, read when its row is opened. */
const GeneTargets: React.FC<{ familyId: string; gene: string; projectId?: string }> = ({
  familyId,
  gene,
  projectId,
}) => {
  const { data, isLoading, isError, error, refetch } = useQuery<NiptGeneTargetsOut>({
    queryKey: ['family', familyId, 'nipt', 'coverage', 'targets', gene, projectId ?? null],
    queryFn: async () => {
      const params: Record<string, string> = { gene };
      if (projectId) params.project_id = projectId;
      const res = await api.get(apiPath`/families/${familyId}/nipt/coverage/targets`, { params });
      return res.data as NiptGeneTargetsOut;
    },
  });
  if (isLoading) {
    return (
      <p className="table-subtle" role="status">
        <span className="viz-loading-spinner" aria-hidden="true" /> Loading the targets of {gene}…
      </p>
    );
  }
  if (isError || !data) {
    return <QueryFailure what={`the targets of ${gene}`} error={error} onRetry={() => void refetch()} />;
  }
  if (!data.targets.length) {
    return <p className="table-subtle">The panel has no target in {gene}.</p>;
  }
  return (
    <table className="analysis-table nipt-coverage-target-table" aria-label={`Targets of ${gene}`}>
      <thead>
        <tr>
          <th>Exon</th>
          <th>Locus</th>
          <th>Mean</th>
          <th>Median</th>
          <th>Min</th>
          <th>Covered</th>
          <th>Target</th>
        </tr>
      </thead>
      <tbody>
        {data.targets.map((target) => (
          <tr key={`${target.chr}:${target.start}-${target.end}`}>
            <td>{targetExon(target.attribute) || '—'}</td>
            {/* BED coordinates: the first base is start + 1. */}
            <td className="table-mono">
              {target.chr}:{(target.start + 1).toLocaleString()}-{target.end.toLocaleString()}
            </td>
            <td className="table-mono">{depth(target.mean)}</td>
            <td className="table-mono">{depth(target.median)}</td>
            <td className="table-mono">{depth(target.min)}</td>
            <td className="table-mono">{coveredPercent(target.proportion_covered)}</td>
            <td>
              {target.weak ? (
                <span className="table-chip table-chip--warning">
                  below · {weakReason(target, data.critical_mean_depth)}
                </span>
              ) : (
                <span className="table-subtle">above</span>
              )}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
};

interface GeneTableProps {
  genes: NiptTargetCoverageGeneOut[];
  familyId: string;
  projectId?: string;
  /** The below-target table counts each gene's weak targets. */
  weakColumn: boolean;
  label: string;
}

/** A gene per row: its targets, how many are weak, and their depth; open a row for its targets. */
const CoverageGeneTable: React.FC<GeneTableProps> = ({ genes, familyId, projectId, weakColumn, label }) => {
  const [open, setOpen] = useState<Set<string>>(() => new Set());
  const [showAll, setShowAll] = useState(false);
  const shown = showAll ? genes : genes.slice(0, GENE_ROWS_SHOWN);
  const columns = weakColumn ? 5 : 4;
  const toggle = (gene: string) =>
    setOpen((current) => {
      const next = new Set(current);
      if (next.has(gene)) next.delete(gene);
      else next.add(gene);
      return next;
    });
  return (
    <>
      <div className="data-table-shell data-table-shell--fit">
        <table className="analysis-table nipt-coverage-table" aria-label={label}>
          <thead>
            <tr>
              <th>Gene</th>
              <th>Targets</th>
              {weakColumn ? <th>Below target</th> : null}
              <th>Lowest mean</th>
              <th>Mean of the targets</th>
            </tr>
          </thead>
          <tbody>
            {shown.map((gene) => {
              const isOpen = open.has(gene.gene);
              return (
                <React.Fragment key={gene.gene}>
                  <tr>
                    <td>
                      {gene.targets > 0 ? (
                        <button
                          type="button"
                          className="button-link nipt-coverage-gene"
                          aria-expanded={isOpen}
                          onClick={() => toggle(gene.gene)}
                        >
                          <svg className="nipt-coverage-caret" viewBox="0 0 6 10" aria-hidden="true">
                            <path d="M1 1l4 4-4 4" />
                          </svg>
                          {gene.gene}
                        </button>
                      ) : (
                        <span className="nipt-coverage-gene">{gene.gene}</span>
                      )}
                    </td>
                    <td>
                      {gene.targets > 0 ? (
                        gene.targets.toLocaleString()
                      ) : (
                        <span className="table-chip table-chip--critical">not a target</span>
                      )}
                    </td>
                    {weakColumn ? <td>{gene.targets > 0 ? gene.weak_targets.toLocaleString() : '—'}</td> : null}
                    <td className="table-mono">{depth(gene.min_mean)}</td>
                    <td className="table-mono">{depth(gene.mean_of_means)}</td>
                  </tr>
                  {isOpen ? (
                    <tr className="nipt-coverage-targets-row">
                      <td colSpan={columns}>
                        <GeneTargets familyId={familyId} gene={gene.gene} projectId={projectId} />
                      </td>
                    </tr>
                  ) : null}
                </React.Fragment>
              );
            })}
          </tbody>
        </table>
      </div>
      {genes.length > shown.length ? (
        <button type="button" className="button-secondary" onClick={() => setShowAll(true)}>
          Show all {genes.length.toLocaleString()} genes
        </button>
      ) : null}
    </>
  );
};

/** Without a target table: the coverage track over each gene or region of the panel. */
const CoverageRegionTable: React.FC<{ regions: ApiNiptCoverageRegion[]; coverage: ApiNiptCoverageSummary; label: string }> = ({
  regions,
  coverage,
  label,
}) => {
  const lowByLabel = new Map(
    (coverage.low_coverage_regions ?? []).map((region) => [`${region.label}:${region.chr}`, region]),
  );
  return (
    <div className="data-table-shell data-table-shell--fit">
      <table className="analysis-table nipt-coverage-table" aria-label={label}>
        <thead>
          <tr>
            <th>Region</th>
            <th>Locus</th>
            <th>Median</th>
            <th>Covered</th>
            <th>Target</th>
          </tr>
        </thead>
        <tbody>
          {regions.map((region) => {
            const low = lowByLabel.get(`${region.label}:${region.chr}`);
            const covered = region.target_bases > 0 ? (100 * region.covered_bases) / region.target_bases : null;
            return (
              <tr key={`${region.label}:${region.chr}:${region.start}`}>
                <td>{region.label}</td>
                <td className="table-mono">
                  {region.chr}:{(region.start + 1).toLocaleString()}-{region.end.toLocaleString()}
                </td>
                <td className="table-mono">{depth(region.median_coverage)}</td>
                <td className="table-mono">{coveredPercent(covered)}</td>
                <td>
                  {low ? (
                    <span className={lowCoverageChipClass(low.reason)}>below · {lowCoverageDetail(low)}</span>
                  ) : (
                    <span className="table-subtle">above</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

/**
 * The plasma's on-target coverage in full, opened from the NIPT page: the genes below the
 * target depth -- a target with a mean below the critical depth, or a base without coverage,
 * where a fetal variant can be missed -- and those above it, each with its targets
 * (REQ-NIPT-006). It reads the genes of the NIPT page's search: its panel and genes.
 */
const FamilyNiptCoveragePage: React.FC = () => {
  const { familyId = '' } = useParams<{ familyId: string }>();
  const location = useLocation();
  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const projectId = searchParams.get('project_id') || undefined;
  const panelId = searchParams.get('panel_id') || undefined;
  const gene = searchParams.get('gene') || undefined;
  const geneTerms = useMemo(() => (gene ? gene.split(GENE_TERM_SPLIT).filter(Boolean) : []), [gene]);
  // Back to the NIPT page as it was left: its filters live in its URL.
  const backTo = (location.state as { from?: string } | null)?.from || `/families/${familyId}/nipt`;
  const [filter, setFilter] = useState('');

  const {
    data: coverage,
    isLoading,
    isError,
    error,
    refetch,
  } = useQuery<ApiNiptCoverageSummary>({
    queryKey: ['family', familyId, 'nipt', 'coverage', 'all-genes', panelId ?? null, gene ?? null, projectId ?? null],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const params: Record<string, string> = { all_genes: 'true' };
      if (projectId) params.project_id = projectId;
      if (panelId) params.panel_id = panelId;
      if (gene) params.gene = gene;
      const res = await api.get(apiPath`/families/${familyId}/nipt/coverage`, { params });
      return res.data as ApiNiptCoverageSummary;
    },
  });

  const { data: panels, isError: panelsFailed } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    enabled: Boolean(panelId),
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

  const header = (body?: React.ReactNode) => {
    const panel = panelId ? panels?.find((item) => item._id === panelId) : undefined;
    const panelName = panelId
      ? panelsFailed || !panels
        ? panelId
        : `${panel?.name?.trim() || panelId}${typeof panel?.version === 'number' ? ` (version ${panel.version})` : ''}`
      : null;
    const scope = [
      panelName ? `gene panel ${panelName}` : null,
      geneTerms.length ? `${geneTerms.length === 1 ? 'gene' : 'genes'} ${geneTerms.join(', ')}` : null,
    ]
      .filter(Boolean)
      .join(' and ');
    return (
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-1">
            <p className="page-kicker">Monogenic NIPT</p>
            <h1 className="catalog-card-title">On-target coverage · Family {familyId}</h1>
            <p className="catalog-card-copy">
              {scope
                ? `The genes of the NIPT search: ${scope}.`
                : 'Every gene of the capture panel: no gene panel or gene was chosen on the NIPT page.'}
            </p>
          </div>
          <div className="inline-actions">
            <Link className="button-secondary" to={backTo}>
              Back to NIPT
            </Link>
          </div>
        </div>
        {body}
      </section>
    );
  };

  if (isLoading) {
    return (
      <PageState
        loading
        kicker="Monogenic NIPT"
        title="Loading coverage"
        message="Reading the plasma's coverage of the capture targets."
      />
    );
  }
  if (isError || !coverage) {
    return (
      <div className="page-shell analysis-shell">
        {header()}
        <QueryFailure what="the coverage" error={error} onRetry={() => void refetch()} />
      </div>
    );
  }

  const term = filter.trim().toUpperCase();
  const matches = (name: string) => !term || name.toUpperCase().includes(term);
  const filterField = (
    <label className="field-label nipt-coverage-filter">
      Find a gene
      <input
        type="search"
        value={filter}
        placeholder="Gene symbol"
        onChange={(event) => setFilter(event.target.value)}
      />
    </label>
  );

  const targets = coverage.targets;
  if (targets) {
    const below = targets.genes.filter((item) => item.weak_targets > 0 || item.targets === 0);
    const above = targets.genes
      .filter((item) => item.weak_targets === 0 && item.targets > 0)
      .sort((a, b) => a.gene.localeCompare(b.gene));
    const belowShown = below.filter((item) => matches(item.gene));
    const aboveShown = above.filter((item) => matches(item.gene));
    return (
      <div className="page-shell analysis-shell">
        {header(
          <div className="page-top-card-body">
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="family-workspace-stat-value">{depth(targets.median_mean)}</span>
                <span className="family-workspace-stat-copy">
                  Median target depth ({targets.targets.toLocaleString()} target{targets.targets === 1 ? '' : 's'})
                </span>
              </div>
              <div className="family-workspace-stat">
                <span className="family-workspace-stat-value">{targets.below_critical.toLocaleString()}</span>
                <span className="family-workspace-stat-copy">targets below {depth(targets.critical_mean_depth)}</span>
              </div>
              <div className="family-workspace-stat">
                <span className="family-workspace-stat-value">{targets.incomplete.toLocaleString()}</span>
                <span className="family-workspace-stat-copy">targets with uncovered bases</span>
              </div>
              <div className="family-workspace-stat">
                <span className="family-workspace-stat-value">{targets.below_advisory.toLocaleString()}</span>
                <span className="family-workspace-stat-copy">targets below {depth(targets.advisory_mean_depth)}</span>
              </div>
            </div>
            {filterField}
          </div>,
        )}

        <section className="surface-card space-y-2" aria-labelledby="nipt-coverage-below">
          <h2 id="nipt-coverage-below" className="section-title">
            Below target <span className="badge-chip">{below.length.toLocaleString()}</span>
          </h2>
          <p className="section-copy">
            Genes with a target whose mean depth is below {depth(targets.critical_mean_depth)}, or with a base
            without coverage: a fetal variant there can be missed. A selected gene the panel does not capture is
            listed as not a target.
          </p>
          {belowShown.length ? (
            <CoverageGeneTable
              key={`below:${term}`}
              genes={belowShown}
              familyId={familyId}
              projectId={projectId}
              weakColumn
              label="Genes below target"
            />
          ) : (
            <p className="table-empty">{below.length ? 'No gene below target matches the search.' : 'No gene below target.'}</p>
          )}
        </section>

        <section className="surface-card space-y-2" aria-labelledby="nipt-coverage-above">
          <h2 id="nipt-coverage-above" className="section-title">
            Above target <span className="badge-chip">{above.length.toLocaleString()}</span>
          </h2>
          <p className="section-copy">
            Genes whose every target reaches {depth(targets.critical_mean_depth)} and is covered over its whole
            length.
          </p>
          {aboveShown.length ? (
            <CoverageGeneTable
              key={`above:${term}`}
              genes={aboveShown}
              familyId={familyId}
              projectId={projectId}
              weakColumn={false}
              label="Genes above target"
            />
          ) : (
            <p className="table-empty">{above.length ? 'No gene above target matches the search.' : 'No gene above target.'}</p>
          )}
        </section>
      </div>
    );
  }

  // No target table: the coverage track over the panel's genes or the family ROI.
  if (coverage.target_region_count === 0) {
    return (
      <div className="page-shell analysis-shell">
        {header(
          <p className="table-subtle page-top-card-body">
            No target regions — set a family ROI or gene panel to report coverage.
          </p>,
        )}
      </div>
    );
  }
  const lowKeys = new Set((coverage.low_coverage_regions ?? []).map((region) => `${region.label}:${region.chr}`));
  const belowRegions = coverage.per_region.filter((region) => lowKeys.has(`${region.label}:${region.chr}`));
  const aboveRegions = coverage.per_region.filter((region) => !lowKeys.has(`${region.label}:${region.chr}`));
  const regionMatches = (region: ApiNiptCoverageRegion) => matches(region.label);
  return (
    <div className="page-shell analysis-shell">
      {header(
        <div className="page-top-card-body">
          <div className="family-workspace-summary">
            <div className="family-workspace-stat">
              <span className="family-workspace-stat-value">{depth(coverage.overall_median_on_target)}</span>
              <span className="family-workspace-stat-copy">
                Median on-target ({coverage.target_region_count} region{coverage.target_region_count === 1 ? '' : 's'})
              </span>
            </div>
            <div className="family-workspace-stat">
              <span className="family-workspace-stat-value">{belowRegions.length.toLocaleString()}</span>
              <span className="family-workspace-stat-copy">below QC</span>
            </div>
          </div>
          {filterField}
        </div>,
      )}
      <section className="surface-card space-y-2" aria-labelledby="nipt-coverage-below">
        <h2 id="nipt-coverage-below" className="section-title">
          Below target <span className="badge-chip">{belowRegions.length.toLocaleString()}</span>
        </h2>
        <p className="section-copy">
          Regions with a median depth below {depth(coverage.min_depth)}, no coverage, or less than{' '}
          {Math.round(coverage.min_covered_fraction * 100)}% of their bases covered.
        </p>
        {belowRegions.filter(regionMatches).length ? (
          <CoverageRegionTable regions={belowRegions.filter(regionMatches)} coverage={coverage} label="Regions below target" />
        ) : (
          <p className="table-empty">No region below target{belowRegions.length ? ' matches the search' : ''}.</p>
        )}
      </section>
      <section className="surface-card space-y-2" aria-labelledby="nipt-coverage-above">
        <h2 id="nipt-coverage-above" className="section-title">
          Above target <span className="badge-chip">{aboveRegions.length.toLocaleString()}</span>
        </h2>
        {aboveRegions.filter(regionMatches).length ? (
          <CoverageRegionTable regions={aboveRegions.filter(regionMatches)} coverage={coverage} label="Regions above target" />
        ) : (
          <p className="table-empty">No region above target{aboveRegions.length ? ' matches the search' : ''}.</p>
        )}
      </section>
    </div>
  );
};

export default FamilyNiptCoveragePage;
