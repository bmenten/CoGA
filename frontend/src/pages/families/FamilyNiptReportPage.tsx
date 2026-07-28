import React, { useMemo } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import PageState from '../../components/PageState';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import type { ApiNiptCoverageSummary, ApiNiptSummary } from '../../lib/apiTypes';
import NiptClassificationBlock from './NiptClassificationBlock';
import {
  CATEGORY_LABELS,
  NIPT_INHERITANCE_GROUPS,
  depth,
  lowCoverageDetail,
  pct,
} from './niptClassification';
import { formatLocus } from './smallVariantResultUtils';
import {
  type FamilyMember,
  type SmallVariant,
  type SmallVariantFamily,
  type SmallVariantPage,
} from './smallVariantSearch';

const MONOGENIC_NIPT_ANALYSIS_TYPE = 'monogenic_nipt';
const REPORT_PAGE_SIZE = 500;

const memberName = (member: FamilyMember): string =>
  member.role?.trim() ? `${member.role} (${member.sample_id})` : member.sample_id;

const variantGeneSort = (a: SmallVariant, b: SmallVariant): number =>
  (a.gene || '').localeCompare(b.gene || '') ||
  (a.nipt?.category ?? 99) - (b.nipt?.category ?? 99);

const ReportVariant: React.FC<{ variant: SmallVariant }> = ({ variant }) => {
  if (!variant.nipt) return null;
  return (
    <article className="surface-card report-variant">
      <div className="report-variant-head">
        <h3 className="section-title">
          {variant.gene || variant.gene_id || 'Intergenic variant'}
          {variant.hgvsc ? ` ${variant.hgvsc}` : ''}
        </h3>
        <span className="table-chip report-classification-chip">
          {variant.nipt.category != null ? `Category ${variant.nipt.category}` : 'Unclassified'}
        </span>
      </div>
      <NiptClassificationBlock nipt={variant.nipt} />
      {variant.review?.note ? (
        <p className="report-paragraph report-note">{variant.review.note}</p>
      ) : null}
      <p className="report-variant-locus">
        {formatLocus(variant)} · {variant.ref || '—'} → {variant.alt || '—'}
      </p>
    </article>
  );
};

const FamilyNiptReportPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const searchParams = useMemo(() => new URLSearchParams(location.search), [location.search]);
  const preferredProjectId = searchParams.get('project_id') || undefined;
  const panelId = searchParams.get('panel_id') || undefined;
  const gene = searchParams.get('gene') || undefined;

  const { data: family, isLoading: familyLoading } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as SmallVariantFamily;
    },
  });

  const isMonogenicNipt =
    (family?.metadata as { analysis_type?: string } | undefined)?.analysis_type ===
    MONOGENIC_NIPT_ANALYSIS_TYPE;

  const members = useMemo<FamilyMember[]>(
    () => ((family?.members as FamilyMember[] | undefined) ?? []).filter((m) => m.active !== false),
    [family],
  );

  const { speciesName, assemblyName, assemblyVersion, isLoading: referenceLoading } =
    useFamilyReference(family?.projects as string[] | undefined, preferredProjectId);
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion },
    'Reference not linked',
  );

  const queryReady = Boolean(familyId && family && isMonogenicNipt);

  const { data: summary } = useQuery<ApiNiptSummary>({
    queryKey: ['family', familyId, 'nipt', 'summary'],
    enabled: queryReady,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/nipt/summary`);
      return res.data as ApiNiptSummary;
    },
  });

  const { data: coverage } = useQuery<ApiNiptCoverageSummary>({
    queryKey: ['family', familyId, 'nipt', 'coverage', panelId ?? null, gene ?? null],
    enabled: queryReady,
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (panelId) params.panel_id = panelId;
      if (gene) params.gene = gene;
      const res = await api.get(`/families/${familyId}/nipt/coverage`, { params });
      return res.data as ApiNiptCoverageSummary;
    },
  });

  const {
    data: variantPage,
    isLoading: variantsLoading,
    isError,
  } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'nipt', 'report-variants', panelId ?? null, gene ?? null],
    enabled: queryReady,
    queryFn: async () => {
      const params: Record<string, string | number> = { page: 1, page_size: REPORT_PAGE_SIZE };
      if (panelId) params.panel_id = panelId;
      if (gene) params.gene = gene;
      const res = await api.get(`/families/${familyId}/nipt/variants`, { params });
      return res.data as SmallVariantPage;
    },
  });

  const candidates = useMemo(
    () => (variantPage?.variants ?? []).filter((variant) => variant.nipt),
    [variantPage],
  );

  // Bucket candidates into the actionable inheritance groups; anything left over
  // (categories 2 / 8 / unclassified) goes to the "Other" catch-all.
  const { grouped, other } = useMemo(() => {
    const claimed = new Set<number>(NIPT_INHERITANCE_GROUPS.flatMap((group) => group.categories));
    const groups = NIPT_INHERITANCE_GROUPS.map((group) => ({
      ...group,
      variants: candidates
        .filter((variant) => variant.nipt?.category != null && group.categories.includes(variant.nipt.category))
        .sort(variantGeneSort),
    }));
    const leftover = candidates
      .filter((variant) => variant.nipt?.category == null || !claimed.has(variant.nipt.category))
      .sort(variantGeneSort);
    return { grouped: groups, other: leftover };
  }, [candidates]);

  if (!familyId) {
    return <PageState kicker="NIPT report" title="Family not specified" />;
  }

  if (familyLoading || (queryReady && variantsLoading) || referenceLoading) {
    return (
      <PageState
        kicker="NIPT report"
        title="Preparing the NIPT report"
        message="Gathering fetal fraction, coverage QC and classified candidates."
      />
    );
  }

  if (family && !isMonogenicNipt) {
    return (
      <PageState
        kicker="NIPT report"
        title="Not a monogenic NIPT family"
        message="This report is only available for families configured for monogenic NIPT analysis."
        action={
          <Link to={`/families/${familyId}`} className="button-secondary">
            Back to family
          </Link>
        }
      />
    );
  }

  if (isError) {
    return (
      <PageState
        kicker="NIPT report"
        title="Report could not be loaded"
        message="The NIPT analysis for this family could not be retrieved."
        action={
          <Link to={`/families/${familyId}/nipt`} className="button-secondary">
            Back to NIPT
          </Link>
        }
      />
    );
  }

  const ff = summary?.fetal_fraction;
  const lowCoverage = coverage?.low_coverage_regions ?? [];

  return (
    <div className="page-shell report-page space-y-6">
      <header className="surface-card report-header">
        <div className="space-y-1">
          <p className="page-kicker">Monogenic NIPT report</p>
          <h1 className="page-state-title">Family {familyId}</h1>
          <p className="report-header-meta">{referenceLabel}</p>
        </div>
        <div className="report-header-actions no-print">
          <Link to={`/families/${familyId}/nipt`} className="button-secondary hover:no-underline">
            Back to NIPT
          </Link>
          <button type="button" className="form-button" onClick={() => window.print()}>
            Print report
          </button>
        </div>
      </header>

      <section className="surface-card report-intro">
        <p className="report-paragraph">
          This report summarises the monogenic NIPT analysis for family <strong>{familyId}</strong>
          {members.length ? `, comprising ${members.map(memberName).join(', ')}` : ''}. It reports
          the estimated fetal fraction, the on-target coverage QC for the interrogated panel, and the{' '}
          {candidates.length} classified candidate variant{candidates.length === 1 ? '' : 's'} grouped
          by inferred inheritance.
        </p>
        <p className="report-disclaimer">
          Monogenic NIPT classifications are decision support derived from cell-free DNA and must be
          confirmed by an invasive diagnostic test and a qualified clinical scientist before clinical
          use.
        </p>
      </section>

      <section className="surface-card report-section">
        <h2 className="section-title">Fetal fraction</h2>
        {ff ? (
          <>
            <p className="report-paragraph">
              The estimated fetal fraction is <strong>{pct(ff.ff)}</strong>
              {ff.ci_low != null && ff.ci_high != null
                ? ` (95% CI ${pct(ff.ci_low)}–${pct(ff.ci_high)})`
                : ''}
              , derived from {ff.n_sites} category-7 site{ff.n_sites === 1 ? '' : 's'} using the{' '}
              {ff.method} method.
            </p>
            {ff.ff_external != null ? (
              <p className="report-paragraph">
                An externally reported fetal fraction of {pct(ff.ff_external)} was also provided
                {ff.disagreement ? ', which disagrees with the computed estimate' : ''}.
              </p>
            ) : null}
            {ff.low_confidence ? (
              <p className="report-paragraph report-disclaimer">
                The fetal-fraction estimate is flagged low-confidence; interpret category calls with
                caution.
              </p>
            ) : null}
          </>
        ) : (
          <p className="report-paragraph">No fetal-fraction estimate is available for this family.</p>
        )}
      </section>

      <section className="surface-card report-section">
        <h2 className="section-title">Coverage QC</h2>
        {coverage && coverage.target_region_count > 0 ? (
          <>
            <p className="report-paragraph">
              Median on-target coverage is <strong>{depth(coverage.overall_median_on_target)}</strong>{' '}
              across {coverage.target_region_count} target region
              {coverage.target_region_count === 1 ? '' : 's'}.
            </p>
            {lowCoverage.length ? (
              <>
                <p className="report-paragraph">
                  {lowCoverage.length} of {coverage.target_region_count} panel gene
                  {coverage.target_region_count === 1 ? '' : 's'} were not adequately interrogated
                  (median &lt; {depth(coverage.min_depth)} or incomplete coverage):
                </p>
                <ul className="report-criteria-list">
                  {lowCoverage.map((region) => (
                    <li key={`${region.label}:${region.chr}`} className="report-paragraph">
                      <strong>{region.label}</strong> — {lowCoverageDetail(region)}
                    </li>
                  ))}
                </ul>
              </>
            ) : (
              <p className="report-paragraph">
                All {coverage.target_region_count} panel gene
                {coverage.target_region_count === 1 ? '' : 's'} met the coverage QC threshold (≥{' '}
                {depth(coverage.min_depth)}).
              </p>
            )}
          </>
        ) : (
          <p className="report-paragraph">
            No target regions were available for coverage QC — set a family ROI or gene panel.
          </p>
        )}
      </section>

      <section className="surface-card report-section">
        <h2 className="section-title">Candidate variants by inheritance</h2>
        {candidates.length === 0 ? (
          <p className="report-paragraph">
            No classified candidate variants were returned for the current panel / gene scope.
          </p>
        ) : (
          <>
            {grouped.map((group) => (
              <div key={group.key} className="report-section report-nipt-group">
                <h3 className="report-subheading">
                  {group.label} ({group.variants.length})
                </h3>
                <p className="report-paragraph">{group.description}</p>
                {group.variants.length ? (
                  group.variants.map((variant) => (
                    <ReportVariant key={variant._id} variant={variant} />
                  ))
                ) : (
                  <p className="report-paragraph report-empty">No candidates in this group.</p>
                )}
              </div>
            ))}
            {other.length ? (
              <div className="report-section report-nipt-group">
                <h3 className="report-subheading">Other categories ({other.length})</h3>
                <p className="report-paragraph">
                  Variants in non-candidate categories (
                  {Array.from(
                    new Set(
                      other.map((variant) =>
                        variant.nipt?.category != null
                          ? CATEGORY_LABELS[variant.nipt.category] || `Category ${variant.nipt.category}`
                          : 'Unclassified',
                      ),
                    ),
                  ).join(', ')}
                  ).
                </p>
                {other.map((variant) => (
                  <ReportVariant key={variant._id} variant={variant} />
                ))}
              </div>
            ) : null}
          </>
        )}
      </section>
    </div>
  );
};

export default FamilyNiptReportPage;
