import React, { useMemo } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import AssemblyScopeBanner from '../../components/AssemblyScopeBanner';
import PageState from '../../components/PageState';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import type { ApiNiptCoverageSummary, ApiNiptSummary, GenePanel } from '../../lib/apiTypes';
import { useReportBuild } from '../../lib/appVersion';
import NiptClassificationBlock from './NiptClassificationBlock';
import ReportSoftwareIdentity from './ReportSoftwareIdentity';
import {
  CATEGORY_LABELS,
  MONOGENIC_NIPT_ANALYSIS_TYPE,
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
import { apiPath } from '../../lib/apiPath';
import { memberLabel } from '../../lib/familyMembers';
import { formatCount, joinWithAnd } from '../../lib/format';

// A report lists one page of candidates, the first ones in genomic order.
const REPORT_PAGE_SIZE = 500;
// The gene query splits into terms as the backend splits it.
const GENE_TERM_SPLIT = /[\s,;]+/;

// Where a list in genomic order stops: the chromosome and position of its last variant.
const stopLocus = (variant: Pick<SmallVariant, 'chr' | 'start'>): string =>
  `${variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`}:${formatCount(variant.start)}`;

// How much of the scope the candidate list covers. It lists the first REPORT_PAGE_SIZE
// candidates of the scope, and the list behind it classifies only the first variants of
// the scope (count_limit): past either, the list stops part-way through the genome.
interface CandidateListExtent {
  listed: number;
  total: number;
  pastReportLimit: boolean;
  classificationCapped: boolean;
  classificationLimit: number | null;
  stopsAt: string | null;
}

const candidateListExtent = (
  page: SmallVariantPage | undefined,
  listed: number,
): CandidateListExtent => {
  const total = Math.max(page?.total ?? 0, listed);
  const pastReportLimit = total > listed;
  const classificationCapped = Boolean(page?.total_is_estimated);
  const pageVariants = page?.variants ?? [];
  // The page comes in genomic order; the groups below re-sort it by gene.
  const last = pageVariants.length ? pageVariants[pageVariants.length - 1] : undefined;
  return {
    listed,
    total,
    pastReportLimit,
    classificationCapped,
    classificationLimit: typeof page?.count_limit === 'number' ? page.count_limit : null,
    stopsAt: (pastReportLimit || classificationCapped) && last ? stopLocus(last) : null,
  };
};

// Why the list is incomplete, where it stops, and what to do about it.
const incompleteListStatement = (extent: CandidateListExtent): string => {
  const { listed, total, pastReportLimit, classificationCapped, classificationLimit, stopsAt } = extent;
  const sentences: string[] = [];
  if (classificationCapped) {
    const limit = classificationLimit != null ? formatCount(classificationLimit) : null;
    const matched = limit
      ? `More than ${limit} variants matched this scope, and CoGA classifies at most ${limit} at once`
      : 'More variants matched this scope than CoGA classifies at once';
    const atLeast = pastReportLimit
      ? `, so this scope holds at least ${formatCount(total)} candidate variants`
      : '';
    sentences.push(`${matched}, in genomic order${atLeast}.`);
  }
  if (pastReportLimit) {
    const reportLimit = `a report lists at most ${formatCount(REPORT_PAGE_SIZE)}`;
    sentences.push(
      classificationCapped
        ? `It shows ${formatCount(listed)} of them: ${reportLimit}.`
        : `It shows ${formatCount(listed)} of the ${formatCount(total)} candidate variants in this scope: ${reportLimit}, in genomic order.`,
    );
  }
  if (stopsAt) sentences.push(`The list stops at ${stopsAt}.`);
  sentences.push('Narrow the scope with a gene panel or a gene on the NIPT page, then open the report again.');
  return sentences.join(' ');
};

// The same, in the few words that head a printout.
const incompleteListNotice = (extent: CandidateListExtent): string =>
  extent.pastReportLimit
    ? `this report lists ${formatCount(extent.listed)} of ${
        extent.classificationCapped ? 'at least' : 'the'
      } ${formatCount(extent.total)} candidate variants in its scope`
    : 'more variants matched the scope of this report than CoGA classifies at once';

// The filters the report applies: the gene panel and the genes of the NIPT page's search.
const scopeLine = ({
  panelId,
  panel,
  panelFailed,
  geneTerms,
}: {
  panelId?: string;
  panel?: GenePanel;
  panelFailed: boolean;
  geneTerms: string[];
}): string => {
  const genes = geneTerms.length
    ? `${geneTerms.length === 1 ? 'gene' : 'genes'} ${joinWithAnd(geneTerms)}`
    : null;
  if (!panelId) {
    return genes
      ? `${genes.charAt(0).toUpperCase()}${genes.slice(1)}.`
      : 'No gene panel or gene was chosen: the report covers every variant of the family.';
  }
  const panelName = panelFailed
    ? `${panelId} (its name could not be loaded)`
    : `${panel?.name?.trim() || panelId}${
        typeof panel?.version === 'number' ? ` (version ${panel.version})` : ''
      }`;
  return genes
    ? `Gene panel ${panelName} and ${genes}: a variant is listed when it matches both.`
    : `Gene panel ${panelName}.`;
};

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
  const geneTerms = useMemo(() => (gene ? gene.split(GENE_TERM_SPLIT).filter(Boolean) : []), [gene]);

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    refetch: refetchFamily,
  } = useQuery<SmallVariantFamily>({
    queryKey: ['family', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
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

  const {
    speciesName,
    assemblyName,
    assemblyValidated,
    assemblyVersion,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects as string[] | undefined, preferredProjectId);
  const referenceLabel = formatResolvedReferenceLabel(
    { speciesName, assemblyName, assemblyVersion, isError: referenceFailed },
    'Reference not linked',
  );

  const queryReady = Boolean(familyId && family && isMonogenicNipt);

  const {
    data: summary,
    isLoading: summaryLoading,
    isError: summaryFailed,
    refetch: refetchSummary,
  } = useQuery<ApiNiptSummary>({
    queryKey: ['family', familyId, 'nipt', 'summary'],
    enabled: queryReady,
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/nipt/summary`);
      return res.data as ApiNiptSummary;
    },
  });

  const {
    data: coverage,
    isLoading: coverageLoading,
    isError: coverageFailed,
    refetch: refetchCoverage,
  } = useQuery<ApiNiptCoverageSummary>({
    queryKey: ['family', familyId, 'nipt', 'coverage', panelId ?? null, gene ?? null],
    enabled: queryReady,
    queryFn: async () => {
      const params: Record<string, string> = {};
      if (panelId) params.panel_id = panelId;
      if (gene) params.gene = gene;
      const res = await api.get(apiPath`/families/${familyId}/nipt/coverage`, { params });
      return res.data as ApiNiptCoverageSummary;
    },
  });

  const {
    data: variantPage,
    isLoading: variantsLoading,
    isError,
    refetch: refetchVariants,
  } = useQuery<SmallVariantPage>({
    queryKey: ['family', familyId, 'nipt', 'report-variants', panelId ?? null, gene ?? null],
    enabled: queryReady,
    queryFn: async () => {
      const params: Record<string, string | number> = { page: 1, page_size: REPORT_PAGE_SIZE };
      if (panelId) params.panel_id = panelId;
      if (gene) params.gene = gene;
      const res = await api.get(apiPath`/families/${familyId}/nipt/variants`, { params });
      return res.data as SmallVariantPage;
    },
  });

  // The report names the gene panel it applies; a name that cannot be loaded is said so.
  const {
    data: panel,
    isLoading: panelLoading,
    isError: panelFailed,
    refetch: refetchPanel,
  } = useQuery<GenePanel>({
    queryKey: ['panel', panelId],
    enabled: Boolean(queryReady && panelId),
    queryFn: async () => {
      const res = await api.get(apiPath`/panels/${panelId}`);
      return res.data as GenePanel;
    },
  });

  const candidates = useMemo(
    () => (variantPage?.variants ?? []).filter((variant) => variant.nipt),
    [variantPage],
  );
  const extent = candidateListExtent(variantPage, candidates.length);
  // Fewer candidates listed than the scope holds: said on screen, in the list, and at the
  // top of a printout. The report used to show the first page as the whole list.
  const listIncomplete = extent.pastReportLimit || extent.classificationCapped;

  // The moment the report was produced, and the build that renders it (TF-15 §1).
  const generatedAt = useMemo(() => new Date(), []);
  const reportBuild = useReportBuild();

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

  if (
    familyLoading ||
    (queryReady && (variantsLoading || summaryLoading || coverageLoading || panelLoading)) ||
    referenceLoading
  ) {
    return (
      <PageState
        kicker="NIPT report"
        title="Preparing the NIPT report"
        message="Gathering fetal fraction, coverage QC and classified candidates."
      />
    );
  }

  // Without the family nothing is requested: the report used to render with no fetal
  // fraction, no coverage and no candidates (#605).
  if (familyFailed) {
    return (
      <PageState
        kicker="NIPT report"
        title="Report could not be loaded"
        message="The family could not be loaded, so the report cannot be prepared. This is not a report without candidates."
        action={
          <button type="button" className="button-secondary" onClick={() => void refetchFamily()}>
            Retry
          </button>
        }
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
        message="The classified candidates for this family could not be retrieved. This is not a report without candidates."
        action={
          <div className="inline-actions">
            <button type="button" className="button-secondary" onClick={() => void refetchVariants()}>
              Retry
            </button>
            <Link to={`/families/${familyId}/nipt`} className="button-secondary">
              Back to NIPT
            </Link>
          </div>
        }
      />
    );
  }

  const ff = summary?.fetal_fraction;
  const lowCoverage = coverage?.low_coverage_regions ?? [];
  // The fetal fraction and the coverage QC are part of what the report states: when either
  // could not be loaded it is said in its place and heads every printed page, instead of
  // "no estimate" or "no target regions" (#605).
  const failedParts = [
    summaryFailed ? 'the fetal-fraction estimate' : null,
    coverageFailed ? 'the coverage QC' : null,
    // The scope names the panel: a printout that cannot name it is not complete.
    panelFailed ? 'the name of the gene panel' : null,
    // A printout that cannot name the build that produced it is not complete either.
    reportBuild.failed ? 'the software version' : null,
  ].filter((part): part is string => Boolean(part));
  const printNoticeParts = [
    failedParts.length ? `${joinWithAnd(failedParts)} could not be loaded` : null,
    listIncomplete ? incompleteListNotice(extent) : null,
  ].filter((part): part is string => Boolean(part));
  const printNotice = printNoticeParts.length
    ? `Incomplete — ${printNoticeParts.join(', and ')}, so this printout does not show ${
        failedParts.length ? 'the whole report' : 'every candidate'
      }.`
    : null;
  const variantsWord = (count: number) => `variant${count === 1 ? '' : 's'}`;
  const candidateCountPhrase = extent.pastReportLimit
    ? `${formatCount(extent.listed)} of ${extent.classificationCapped ? 'at least' : 'the'} ${formatCount(
        extent.total,
      )} classified candidate variants in its scope,`
    : `the ${extent.classificationCapped ? 'first ' : ''}${formatCount(extent.listed)} classified candidate ${variantsWord(
        extent.listed,
      )} ${extent.classificationCapped ? 'of' : 'in'} its scope,`;

  return (
    <div className="page-shell report-page space-y-6">
      {printNotice ? <p className="report-print-notice print-only">{printNotice}</p> : null}
      <header className="surface-card report-header">
        <div className="space-y-1">
          <p className="page-kicker">Monogenic NIPT report</p>
          <h1 className="page-state-title">Family {familyId}</h1>
          <p className="report-header-meta">{referenceLabel}</p>
          <AssemblyScopeBanner
            assemblyName={assemblyName}
            assemblyValidated={assemblyValidated}
            unavailable={referenceFailed}
            onRetry={retryReference}
          />
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

      {failedParts.length ? (
        <section className="surface-card report-incomplete no-print" role="alert">
          <p className="report-paragraph">
            <strong>Parts of this report could not be loaded:</strong> {joinWithAnd(failedParts)}.
            A printout says the report is incomplete.{' '}
            <button
              type="button"
              className="button-link"
              onClick={() => {
                if (summaryFailed) void refetchSummary();
                if (coverageFailed) void refetchCoverage();
                if (panelFailed) void refetchPanel();
                if (reportBuild.failed) reportBuild.retry();
              }}
            >
              Retry
            </button>
          </p>
        </section>
      ) : null}

      {listIncomplete ? (
        <section className="surface-card report-incomplete no-print" role="alert">
          <p className="report-paragraph">
            <strong>The candidate list is incomplete:</strong> {incompleteListNotice(extent)}.
            The list says why, and a printout says the report is incomplete.
          </p>
        </section>
      ) : null}

      <section className="surface-card report-intro">
        <p className="report-paragraph">
          This report summarises the monogenic NIPT analysis for family <strong>{familyId}</strong>
          {members.length ? `, comprising ${members.map(memberLabel).join(', ')}` : ''}. It reports
          the estimated fetal fraction, the on-target coverage QC for the interrogated panel, and{' '}
          {candidateCountPhrase} grouped by inferred inheritance.
        </p>
        <p className="report-disclaimer">
          Monogenic NIPT classifications are decision support derived from cell-free DNA and must be
          confirmed by an invasive diagnostic test and a qualified clinical scientist before clinical
          use.
        </p>
      </section>

      <section className="surface-card report-section">
        <h2 className="section-title">Scope</h2>
        <p className="report-paragraph">{scopeLine({ panelId, panel, panelFailed, geneTerms })}</p>
        <p className="report-paragraph">
          The candidate list holds every variant in this scope with a call in the cfDNA sample,
          except the sites on the recurrent-artifact list of the assay. No other filter of the NIPT
          page applies: not the categories, the inheritance preset, the confidence, the regions, or
          the frequency and consequence filters. Sites that fail the quality filter are listed too:
          a low-depth site carries the low_depth flag, a low-QUAL site no flag.
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
            {ff.low_confidence ? (
              <p className="report-paragraph report-disclaimer">
                The fetal-fraction estimate is flagged low-confidence; interpret category calls with
                caution.
              </p>
            ) : null}
          </>
        ) : summaryFailed ? (
          <p className="report-paragraph" role="alert">
            The fetal-fraction estimate could not be loaded, and with it any low-confidence
            warning. Do not interpret the category calls without it.
          </p>
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
        ) : coverageFailed ? (
          <p className="report-paragraph" role="alert">
            The coverage QC could not be loaded, so it is not known which panel genes were
            adequately interrogated.
          </p>
        ) : (
          <p className="report-paragraph">
            No target regions were available for coverage QC — set a family ROI or gene panel.
          </p>
        )}
      </section>

      <section className="surface-card report-section">
        <h2 className="section-title">Candidate variants by inheritance</h2>
        {/* Printed with the list: a list that stops part-way through the genome says so. */}
        {listIncomplete ? (
          <p className="report-paragraph report-incomplete-list">
            <strong>This list is incomplete.</strong> {incompleteListStatement(extent)}
          </p>
        ) : null}
        {candidates.length === 0 ? (
          listIncomplete ? null : (
            <p className="report-paragraph">
              No classified candidate variants were returned for this scope.
            </p>
          )
        ) : (
          <>
            {grouped.map((group) => (
              <div key={group.key} className="report-section report-nipt-group">
                {/* A group counts what the report lists, not what the scope holds. */}
                <h3 className="report-subheading">
                  {group.label} ({group.variants.length}
                  {listIncomplete ? ' listed' : ''})
                </h3>
                <p className="report-paragraph">{group.description}</p>
                {group.variants.length ? (
                  group.variants.map((variant) => (
                    <ReportVariant key={variant._id} variant={variant} />
                  ))
                ) : (
                  <p className="report-paragraph report-empty">
                    {listIncomplete ? 'None among the listed variants.' : 'No candidates in this group.'}
                  </p>
                )}
              </div>
            ))}
            {other.length ? (
              <div className="report-section report-nipt-group">
                <h3 className="report-subheading">
                  Other categories ({other.length}
                  {listIncomplete ? ' listed' : ''})
                </h3>
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

      <footer className="surface-card report-footer">
        <p className="report-footer-timestamp">
          Report generated {generatedAt.toISOString().replace('T', ' ').slice(0, 16)} UTC
        </p>
        <ReportSoftwareIdentity reportBuild={reportBuild} />
      </footer>
    </div>
  );
};

export default FamilyNiptReportPage;
