import React, { useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiFamilyMember,
  ApiFamilyParaphaseTable,
  ApiFamilyRecord,
  ApiParaphaseGeneResult,
  ApiParaphaseSampleResult,
} from '../../lib/apiTypes';
import FamilyCaseHeader from './FamilyCaseHeader';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import { apiPath } from '../../lib/apiPath';
import { raw, routePath } from '../../lib/routePath';
import GeneDiseaseHeading from '../../components/GeneDiseaseHeading';
import InfoIcon from '../../components/InfoIcon';
import InfoTip from '../../components/InfoTip';
import { geneInfoHref } from '../../lib/geneInfo';
import { omimGeneHref } from '../../lib/omim';
import FindingCard, { FindingLinks } from './FindingCard';
import FamilyGenotypeGrid, { type GenotypeTone } from './FamilyGenotypeGrid';
import SampleLabel from '../../components/SampleLabel';
import type { FindingReview } from './findingReview';
import { useFindingReviewCards, type FindingReviewCardParts } from './useFindingReviewCards';



const formatNullableNumber = (value?: number | null, digits = 0): string => {
  if (value == null || Number.isNaN(value)) return 'n/a';
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
};

// The backend decides what a copy-number change is: the normal copy number depends on
// the region (NEB 6, a gene + pseudogene pair 4, ...) and, on chrX, on the sample's sex.
// Re-deriving it here against a flat 2 would flag nearly every region. Shared by the
// "Copy-number changes only" filter and the count.
// A member's call to look at whatever its copy number: pathogenic, carrier, or review (which a
// fusion, or a value no rule of the region names, gives without any copy-number change).
const ABNORMAL_CALL_STATUSES = new Set(['pathogenic', 'carrier', 'review']);

const geneHasAbnormalCall = (gene: ApiParaphaseGeneResult): boolean =>
  Object.values(gene.samples).some((call) => ABNORMAL_CALL_STATUSES.has(call?.clinical_status ?? ''));

const geneHasCopyNumberSignal = (gene: ApiParaphaseGeneResult): boolean =>
  Boolean(gene.has_copy_number_signal) ||
  Object.values(gene.samples).some((call) => Boolean(call?.copy_number_signal));

const formatMetricValue = (value?: number | null, digits = 0): string =>
  value == null ? 'no-call' : formatNullableNumber(value, digits);

// The backend lists total_cn, gene_cn and highest_total_cn here whenever the call has them,
// so an empty list means the call has none.
const sampleCopyNumberMetrics = (call: ApiParaphaseSampleResult) => call.copy_number_metrics ?? [];

const clinicalCopyNumberMetrics = (
  gene: ApiParaphaseGeneResult,
  call: ApiParaphaseSampleResult,
) => {
  const keys = new Set(gene.region_info?.key_copy_number_fields || []);
  if (!keys.size) return sampleCopyNumberMetrics(call);
  return sampleCopyNumberMetrics(call).filter((metric) => keys.has(metric.key));
};

const nonClinicalCopyNumberMetrics = (
  gene: ApiParaphaseGeneResult,
  call: ApiParaphaseSampleResult,
) => {
  const clinicalKeys = new Set(clinicalCopyNumberMetrics(gene, call).map((metric) => metric.key));
  return sampleCopyNumberMetrics(call).filter((metric) => !clinicalKeys.has(metric.key));
};

const clinicalReadMetrics = (
  gene: ApiParaphaseGeneResult,
  call: ApiParaphaseSampleResult,
) => {
  const keys = new Set(gene.region_info?.key_read_fields || []);
  if (!keys.size) return [];
  return (call.read_metrics || []).filter((metric) => keys.has(metric.key));
};

const hasClinicalNoCall = (gene: ApiParaphaseGeneResult, call: ApiParaphaseSampleResult) =>
  clinicalCopyNumberMetrics(gene, call).some((metric) => metric.value == null);

const visibleHaplotypeGroups = (call: ApiParaphaseSampleResult) =>
  (call.haplotype_groups || []).filter(
    (group) => group.key !== 'assembled_haplotypes' && group.key !== 'final_haplotypes',
  );

const formatRegionDepth = (depth: Record<string, unknown>): string => {
  const entries = Object.entries(depth)
    .filter(([, value]) => typeof value === 'number' || typeof value === 'string')
    .slice(0, 2);
  if (!entries.length) return 'n/a';
  return entries
    .map(([key, value]) => {
      const label = key.replace(/_/g, ' ');
      const formatted =
        typeof value === 'number' ? formatNullableNumber(value, 1) : String(value);
      return `${label} ${formatted}`;
    })
    .join(' · ');
};

const sampleCallCount = (genes: ApiParaphaseGeneResult[]): number =>
  genes.reduce((total, gene) => total + Object.keys(gene.samples).length, 0);

const formatExtraFieldValue = (value: unknown): string => {
  if (value == null) return 'n/a';
  if (typeof value === 'boolean') return value ? 'yes' : 'no';
  if (typeof value === 'number') return formatNullableNumber(value, Number.isInteger(value) ? 0 : 2);
  if (typeof value === 'string') return value || 'n/a';
  if (Array.isArray(value)) {
    if (!value.length) return 'n/a';
    const rendered = value
      .slice(0, 6)
      .map((item) => formatExtraFieldValue(item))
      .join(', ');
    return value.length > 6 ? `${rendered} +${value.length - 6} more` : rendered;
  }
  if (typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>).filter(
      ([, item]) => item != null && item !== '',
    );
    if (!entries.length) return 'n/a';
    const rendered = entries
      .slice(0, 6)
      .map(([key, item]) => `${key}: ${formatExtraFieldValue(item)}`)
      .join(' · ');
    return entries.length > 6 ? `${rendered} +${entries.length - 6} more` : rendered;
  }
  return String(value);
};

const EXTRA_LIST_MAX = 6;

// A list value gives each item a line of its own: an RCCX allele label holds commas of its own
// ("deletion_P31L,G111Vfs"), so items joined by commas could not be told apart.
const ExtraFieldValue: React.FC<{ value: unknown }> = ({ value }) => {
  if (!Array.isArray(value) || value.length < 2) return <strong>{formatExtraFieldValue(value)}</strong>;
  return (
    <strong className="paraphase-extra-list">
      {value.slice(0, EXTRA_LIST_MAX).map((item, index) => (
        <span key={index}>{formatExtraFieldValue(item)}</span>
      ))}
      {value.length > EXTRA_LIST_MAX ? <span>+{value.length - EXTRA_LIST_MAX} more</span> : null}
    </strong>
  );
};

// Paraphase reports the analysed region per call as e.g. "38:chr5:70917100-70961220".
const parsePhaseRegion = (
  phaseRegion?: string | null,
): { chrom: string; start: number; end: number } | null => {
  if (!phaseRegion) return null;
  const match = phaseRegion.match(/chr([\w]+):(\d+)-(\d+)/i);
  if (!match) return null;
  return { chrom: match[1], start: Number(match[2]), end: Number(match[3]) };
};

const buildParaphaseChromosomeHref = (
  familyId: string,
  region: { chrom: string; start: number; end: number },
  projectId?: string | null,
): string => {
  const params = new URLSearchParams({ start: String(region.start), end: String(region.end) });
  if (projectId) params.set('project_id', projectId);
  return routePath`/families/${familyId}/chromosome/${region.chrom}${raw(`?${params.toString()}`)}`;
};

// IGV opens on the analysed region plus a little flank, so the edges of the segmental
// duplication are visible rather than flush against the view — the same 1 kb the
// structural-variant and repeat links use.
const IGV_PADDING_BP = 1_000;

const formatParaphaseRegion = (region: { chrom: string; start: number; end: number }) =>
  `chr${region.chrom}:${region.start.toLocaleString()}-${region.end.toLocaleString()}`;

const getParaphaseIgvLocus = (region: { chrom: string; start: number; end: number }) =>
  `chr${region.chrom}:${Math.max(1, region.start - IGV_PADDING_BP)}-${
    Math.max(region.start, region.end) + IGV_PADDING_BP
  }`;

const buildParaphaseIgvHref = (
  familyId: string,
  region: { chrom: string; start: number; end: number },
  projectId?: string | null,
): string => {
  const params = new URLSearchParams({ locus: getParaphaseIgvLocus(region) });
  if (projectId) params.set('project_id', projectId);
  params.set('back_path', routePath`/families/${familyId}/paraphase`);
  return routePath`/families/${familyId}/igv${raw(`?${params.toString()}`)}`;
};

const CLINICAL_STATUS_META: Record<string, { label: string; className: string }> = {
  pathogenic: { label: 'Pathogenic', className: 'paraphase-status--pathogenic' },
  carrier: { label: 'Carrier', className: 'paraphase-status--carrier' },
  normal: { label: 'Normal', className: 'paraphase-status--normal' },
  review: { label: 'Review', className: 'paraphase-status--review' },
  no_call: { label: 'No-call', className: 'paraphase-status--nocall' },
  none: { label: '', className: '' },
};

// The verbose, locus-agnostic technical readout — hidden behind "Extra info".
const ParaphaseSampleDetail: React.FC<{
  gene: ApiParaphaseGeneResult;
  call: ApiParaphaseSampleResult;
}> = ({ gene, call }) => {
  const otherCnMetrics = gene.is_medically_relevant
    ? nonClinicalCopyNumberMetrics(gene, call)
    : sampleCopyNumberMetrics(call);
  const highlightedReadKeys = new Set(gene.region_info?.key_read_fields || []);
  const highlightedReadMetrics = clinicalReadMetrics(gene, call);
  const secondaryReadMetrics =
    call.read_metrics?.filter((metric) => !highlightedReadKeys.has(metric.key)) || [];
  return (
    <div className="paraphase-sample-detail">
      <SampleLabel sampleId={call.sample} role={call.role} />
      {otherCnMetrics.length > 0 && (
        <div className="paraphase-cn-grid">
          {otherCnMetrics.map((metric) => (
            <span key={metric.key}>
              {metric.label} <strong>{formatMetricValue(metric.value)}</strong>
            </span>
          ))}
        </div>
      )}
      {highlightedReadMetrics.length > 0 && (
        <div className="paraphase-read-grid paraphase-read-grid--clinical">
          {highlightedReadMetrics.map((metric) => (
            <span key={metric.key}>
              {metric.label} <strong>{formatMetricValue(metric.value)}</strong>
            </span>
          ))}
        </div>
      )}
      {secondaryReadMetrics.length > 0 && (
        <div className="paraphase-read-grid">
          {secondaryReadMetrics.map((metric) => (
            <span key={metric.key}>
              {metric.label} <strong>{formatMetricValue(metric.value)}</strong>
            </span>
          ))}
        </div>
      )}
      {!!call.extra_fields?.length && (
        <div className="paraphase-extra-fields">
          {call.extra_fields.map((field) => (
            <div key={field.key} className="paraphase-extra-field">
              <div>
                <span>{field.label}</span>
                {field.description && <p>{field.description}</p>}
              </div>
              <ExtraFieldValue value={field.value} />
            </div>
          ))}
        </div>
      )}
      <div className="table-subtle">
        {call.final_haplotype_count} final haplotypes · {call.assembled_haplotype_count} assembled ·{' '}
        {call.variant_site_count} phase sites
      </div>
      {visibleHaplotypeGroups(call).length > 0 && (
        <div className="paraphase-haplotype-groups">
          {visibleHaplotypeGroups(call).map((group) => (
            <div key={group.key} className="paraphase-haplotype-group">
              <span>{group.label}</span>
              <strong>{group.count}</strong>
              <div>
                {group.haplotypes.slice(0, 6).join(', ')}
                {group.haplotypes.length > 6 ? ` +${group.haplotypes.length - 6} more` : ''}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="table-subtle">
        Depth {formatRegionDepth(call.region_depth)} · genome{' '}
        {formatNullableNumber(call.genome_depth, 1)}
      </div>
      {call.phase_region && <div className="table-subtle">{call.phase_region}</div>}
    </div>
  );
};

const PARAPHASE_STATUS_TONE: Record<string, GenotypeTone> = {
  pathogenic: 'alert',
  carrier: 'warn',
  review: 'review',
  normal: 'ref',
  no_call: 'na',
  none: 'ref',
};

// The copy-number columns of a region's genotype grid: its key copy numbers, in the order the
// members' calls list them; the first is the call, the others its measurements.
const regionCopyNumberColumns = (
  gene: ApiParaphaseGeneResult,
  members: { sample_id: string }[],
): { key: string; label: string }[] => {
  const columns = new Map<string, string>();
  members.forEach((member) => {
    const call = gene.samples[member.sample_id];
    if (call) clinicalCopyNumberMetrics(gene, call).forEach((metric) => columns.set(metric.key, metric.label));
  });
  return Array.from(columns, ([key, label]) => ({ key, label }));
};

// A saved review in the cached Paraphase table, at its region.
const writeParaphaseReview = (
  table: ApiFamilyParaphaseTable,
  key: string,
  review: FindingReview | null,
): ApiFamilyParaphaseTable => ({
  ...table,
  genes: table.genes.map((gene) => (gene.gene_symbol === key ? { ...gene, review } : gene)),
});

// What a field is called on the card: its label in a member's call, else its key in words.
const extraFieldLabels = (gene: ApiParaphaseGeneResult): Map<string, string> => {
  const labels = new Map<string, string>();
  Object.values(gene.samples).forEach((call) =>
    (call.extra_fields || []).forEach((field) => labels.set(field.key, field.label)),
  );
  return labels;
};

const ParaphaseGuideTerms: React.FC<{ terms: { term: string; text: string }[]; plain?: boolean }> = ({
  terms,
  plain = false,
}) => (
  <dl className={`paraphase-guide-terms${plain ? ' paraphase-guide-terms--plain' : ''}`}>
    {terms.map((term) => (
      <React.Fragment key={term.term}>
        {/* A long label (duplication_WT_plus_<variant>) wraps after an underscore. */}
        <dt>
          {term.term.split('_').map((part, index) => (
            <React.Fragment key={index}>
              {index > 0 && (
                <>
                  _<wbr />
                </>
              )}
              {part}
            </React.Fragment>
          ))}
        </dt>
        <dd>{term.text}</dd>
      </React.Fragment>
    ))}
  </dl>
);

/**
 * How to read a region: what it is, how its calls read, what each column and extra field
 * holds, and the catalogue's own guide (the RCCX module's allele labels). Explanation, the
 * same for every family, so it opens on request; this family's calls stay on the card.
 */
const ParaphaseReadingGuide: React.FC<{
  gene: ApiParaphaseGeneResult;
  title: string;
  columns: { key: string; label: string }[];
}> = ({ gene, title, columns }) => {
  const region = gene.region_info;
  if (!region) return null;
  const clinical = region.clinical;
  const descriptions = region.field_descriptions || {};
  const labels = extraFieldLabels(gene);
  const columnTerms = columns
    .filter((column) => descriptions[column.key])
    .map((column) => ({ term: column.label, text: descriptions[column.key] }));
  const extraTerms = (region.key_extra_fields || [])
    .filter((key) => descriptions[key])
    .map((key) => ({ term: labels.get(key) ?? key.replace(/_/g, ' '), text: descriptions[key] }));
  const callTerms = [
    { term: 'Normal', text: clinical?.normal },
    { term: 'Carrier', text: clinical?.carrier },
    { term: 'Pathogenic', text: clinical?.pathogenic },
  ].filter((entry): entry is { term: string; text: string } => Boolean(entry.text));
  return (
    <section className="paraphase-guide" aria-label={`How to read ${title}`}>
      {(region.summary || clinical?.interpretation) && (
        <div className="paraphase-guide-section paraphase-guide-section--wide">
          {region.summary && <p>{region.summary}</p>}
          {clinical?.interpretation && <p>{clinical.interpretation}</p>}
        </div>
      )}
      {(callTerms.length > 0 || clinical?.caveats) && (
        <div className="paraphase-guide-section">
          <h3>Calls</h3>
          {callTerms.length > 0 && <ParaphaseGuideTerms terms={callTerms} plain />}
          {clinical?.caveats && (
            <p className="paraphase-guide-caveat">
              <strong>Caveat:</strong> {clinical.caveats}
            </p>
          )}
        </div>
      )}
      {(columnTerms.length > 0 || extraTerms.length > 0) && (
        <div className="paraphase-guide-section">
          <h3>Fields</h3>
          {columnTerms.length > 0 && <ParaphaseGuideTerms terms={columnTerms} plain />}
          {extraTerms.length > 0 && (
            <>
              <p className="paraphase-guide-subhead">Under Extra info</p>
              <ParaphaseGuideTerms terms={extraTerms} plain />
            </>
          )}
        </div>
      )}
      {(region.guide || []).map((section) => (
        <div key={section.heading} className="paraphase-guide-section">
          <h3>{section.heading}</h3>
          {section.text && <p>{section.text}</p>}
          {section.terms.length > 0 && <ParaphaseGuideTerms terms={section.terms} />}
        </div>
      ))}
    </section>
  );
};

// The gene page of each gene, linked by its name.
const GeneLinks: React.FC<{
  genes: string[];
  separator: string;
  familyId: string;
  projectId?: string | null;
  className?: string;
}> = ({ genes, separator, familyId, projectId, className }) => (
  <>
    {genes.map((symbol, index) => (
      <React.Fragment key={symbol}>
        {index > 0 && separator}
        <Link to={geneInfoHref(symbol, familyId, projectId) ?? '/genes'} className={className}>
          {symbol}
        </Link>
      </React.Fragment>
    ))}
  </>
);

const ParaphaseLocusCard: React.FC<{
  gene: ApiParaphaseGeneResult;
  members: ApiFamilyMember[];
  familyId: string;
  projectId?: string | null;
  review: FindingReviewCardParts;
}> = ({ gene, members, familyId, projectId, review }) => {
  const [showExtra, setShowExtra] = useState(false);
  const [showGuide, setShowGuide] = useState(false);
  const region = gene.region_info;
  const clinical = region?.clinical;
  const calls = Object.values(gene.samples);
  const anyPathogenic = calls.some((call) => call.clinical_status === 'pathogenic');
  const anyCarrier = calls.some((call) => call.clinical_status === 'carrier');
  const phaseRegion = calls.map((call) => parsePhaseRegion(call.phase_region)).find(Boolean) || null;
  const title = region?.display_name || gene.gene_symbol;
  // The region's note on a null copy-number call, shown whenever a member's key copy number
  // is one: it says why the call is missing, so it stays on the card.
  const noCallNote =
    region?.notes?.length && calls.some((call) => hasClinicalNoCall(gene, call)) ? region.notes[0] : null;
  const copyNumberColumns = regionCopyNumberColumns(gene, members);
  const [callColumn, ...metricColumns] = copyNumberColumns;
  // A catalogue region's genes are HGNC symbols; Paraphase's own key (smn1, RCCX) names no
  // gene. A region of one gene links its title to it; one named after its genes (SMN1/SMN2)
  // links each name in its title; one with a name of its own (RCCX module) links its genes
  // under Genes, its title standing unlinked. OMIM takes the first gene.
  const genes = region?.genes?.length ? region.genes : [gene.gene_symbol];
  const primaryGene = genes[0];
  const titleIsGenes = genes.length > 1 && genes.join('/') === title;
  return (
    <FindingCard
      aria-label={`${title} region`}
      rail={anyPathogenic ? 'pathogenic' : anyCarrier ? 'warning' : 'neutral'}
      className={`paraphase-card${review.excluded ? ' variant-card--excluded' : ''}`}
      folded={review.folded}
      excludedBar={review.excludedBar}
      subhead={review.subhead}
      heading={
        <GeneDiseaseHeading
          gene={
            titleIsGenes ? (
              <GeneLinks
                genes={genes}
                separator="/"
                familyId={familyId}
                projectId={projectId}
                className="variant-card-gene"
              />
            ) : (
              title
            )
          }
          geneHref={genes.length === 1 ? geneInfoHref(primaryGene, familyId, projectId) : null}
          diseases={(region?.disorders || []).map((disorder) => ({
            name: disorder.name,
            omimUrl: disorder.omim_url,
          }))}
        />
      }
      tags={
        gene.is_medically_relevant ? (
          <span className="variant-card-chip variant-card-chip--accent">Clinical</span>
        ) : null
      }
      columns={[
        {
          key: 'variant',
          title: 'Region',
          content: (
            <dl className="variant-card-mini-dl">
              <div>
                <dt>Locus</dt>
                <dd>{phaseRegion ? formatParaphaseRegion(phaseRegion) : '—'}</dd>
              </div>
              {/* The genes a region spans, when its name is not already them. */}
              {genes.join('/') !== title && (
                <div>
                  <dt>Genes</dt>
                  <dd>
                    <GeneLinks
                      genes={genes}
                      separator=", "
                      familyId={familyId}
                      projectId={projectId}
                      className="variant-card-gene-fact-link"
                    />
                  </dd>
                </div>
              )}
              {clinical?.caveats && (
                // The caveat applies to every call in the region: its mark stays on the card,
                // its full text is in the tooltip.
                <div>
                  <dt>Caveat</dt>
                  <dd>
                    <InfoTip
                      staticChild
                      label={clinical.caveats}
                      className="variant-card-chip variant-card-chip--warning paraphase-caveat-chip"
                    >
                      Read before interpreting
                    </InfoTip>
                  </dd>
                </div>
              )}
            </dl>
          ),
        },
        {
          key: 'genotypes',
          title: 'Genotypes',
          content: (
            <FamilyGenotypeGrid
              callLabel={callColumn?.label ?? 'Copy number'}
              columns={metricColumns.map((column) => ({ ...column, numeric: true }))}
              withStatus
              entries={members.map((member) => {
                const call = gene.samples[member.sample_id];
                if (!call) return { member, tone: 'na' as const, call: 'No data' };
                const status = call.clinical_status || 'none';
                const meta = CLINICAL_STATUS_META[status] || CLINICAL_STATUS_META.none;
                const metrics = new Map(
                  clinicalCopyNumberMetrics(gene, call).map((metric) => [metric.key, metric.value]),
                );
                return {
                  member,
                  tone: PARAPHASE_STATUS_TONE[status] ?? 'ref',
                  call: callColumn ? formatMetricValue(metrics.get(callColumn.key)) : 'CN n/a',
                  values: Object.fromEntries(
                    metricColumns.map((column) => [column.key, formatMetricValue(metrics.get(column.key))]),
                  ),
                  status: meta.label ? { label: meta.label, tone: PARAPHASE_STATUS_TONE[status] ?? 'ref' } : null,
                };
              })}
            />
          ),
        },
      ]}
      note={
        noCallNote || review.note ? (
          <>
            {noCallNote ? <p className="paraphase-clinical-note">{noCallNote}</p> : null}
            {review.note}
          </>
        ) : null
      }
      actions={
        <>
          {review.actions}
          {region && (
            <button
              type="button"
              className="variant-review-link paraphase-guide-toggle"
              aria-expanded={showGuide}
              onClick={() => setShowGuide((value) => !value)}
            >
              {showGuide ? 'Hide how to read' : 'How to read'}
            </button>
          )}
          <button
            type="button"
            className="variant-review-link paraphase-extra-toggle"
            aria-expanded={showExtra}
            onClick={() => setShowExtra((value) => !value)}
          >
            {showExtra ? 'Hide extra info' : 'Extra info'}
          </button>
        </>
      }
      links={
        <FindingLinks
          igv={
            phaseRegion
              ? {
                  href: buildParaphaseIgvHref(familyId, phaseRegion, projectId),
                  label: `Open IGV at ${getParaphaseIgvLocus(phaseRegion)}`,
                }
              : null
          }
          chromosome={
            phaseRegion
              ? {
                  href: buildParaphaseChromosomeHref(familyId, phaseRegion, projectId),
                  label: `Open chromosome view at ${formatParaphaseRegion(phaseRegion)}`,
                }
              : null
          }
          omim={omimGeneHref(primaryGene)}
        />
      }
      more={
        showGuide || showExtra ? (
          <>
            {showGuide && (
              <div className="paraphase-card-extra">
                <ParaphaseReadingGuide gene={gene} title={title} columns={copyNumberColumns} />
              </div>
            )}
            {showExtra && (
              <div className="paraphase-card-extra">
                {members.map((member) => {
                  const call = gene.samples[member.sample_id];
                  return call ? <ParaphaseSampleDetail key={member.sample_id} gene={gene} call={call} /> : null;
                })}
              </div>
            )}
          </>
        ) : null
      }
    />
  );
};

const FamilyParaphasePage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const [searchText, setSearchText] = useState('');
  // Ticked on open: the regions with a copy-number change or an abnormal call. Unticking shows
  // every region of the scope.
  const [onlyCopyNumberSignals, setOnlyCopyNumberSignals] = useState(true);
  const [regionScope, setRegionScope] = useState<'clinical' | 'all'>('clinical');
  const projectIdParam = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}`);
      return response.data as ApiFamilyRecord;
    },
  });

  const {
    assemblyName,
    assemblyValidated,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectIdParam);

  const {
    data: paraphaseTable,
    isLoading: paraphaseLoading,
    isError: paraphaseFailed,
    error: paraphaseError,
    refetch: refetchParaphase,
  } = useQuery<ApiFamilyParaphaseTable>({
      queryKey: ['family', familyId, 'paraphase', resolvedProjectId],
      queryFn: async () => {
        const response = await api.get(apiPath`/families/${familyId}/paraphase`, {
          params: resolvedProjectId ? { project_id: resolvedProjectId } : undefined,
        });
        return response.data as ApiFamilyParaphaseTable;
      },
      enabled: Boolean(familyId && (!family?.projects?.length || !referenceLoading)),
    });

  const orderedMembers = useMemo(
    () => sortFamilyMembersProbandFirst(paraphaseTable?.samples || family?.members || []),
    [family?.members, paraphaseTable?.samples],
  );
  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, isError: referenceFailed },
    'Not linked',
  );

  const filteredGenes = useMemo(() => {
    const query = searchText.trim().toLowerCase();
    return (paraphaseTable?.genes || []).filter((gene) => {
      const queryHaystack = [
        gene.gene_symbol,
        gene.region_info?.display_name,
        gene.region_info?.summary,
        ...(gene.region_info?.genes || []),
        ...(gene.region_info?.disorders || []).map((disorder) => disorder.name),
      ]
        .filter(Boolean)
        .join(' ')
        .toLowerCase();
      if (query && !queryHaystack.includes(query)) {
        return false;
      }
      if (regionScope === 'clinical' && !gene.is_medically_relevant) {
        return false;
      }
      if (!onlyCopyNumberSignals) return true;
      // A call that reads abnormal stays in view without a copy-number change (a fusion's review).
      return geneHasCopyNumberSignal(gene) || geneHasAbnormalCall(gene);
    });
  }, [onlyCopyNumberSignals, paraphaseTable?.genes, regionScope, searchText]);

  const copyNumberSignalCount = useMemo(
    () =>
      (paraphaseTable?.genes || []).filter(geneHasCopyNumberSignal).length,
    [paraphaseTable?.genes],
  );
  const medicallyRelevantCount = useMemo(
    () => (paraphaseTable?.genes || []).filter((gene) => gene.is_medically_relevant).length,
    [paraphaseTable?.genes],
  );
  const paraphaseQueryKey = useMemo(
    () => ['family', familyId, 'paraphase', resolvedProjectId],
    [familyId, resolvedProjectId],
  );
  const reviews = useFindingReviewCards<ApiFamilyParaphaseTable>({
    kind: 'paraphase',
    familyId,
    projectId: resolvedProjectId,
    queryKey: paraphaseQueryKey,
    write: writeParaphaseReview,
    readOnly: Boolean(family?.read_only),
  });

  if (familyLoading || paraphaseLoading || (family?.projects?.length && referenceLoading)) {
    return (
      <PageState
        loading
        kicker="Paraphase"
        title="Loading Paraphase results"
        message="Preparing duplicated-region results and pedigree context."
      />
    );
  }

  if (familyFailed || paraphaseFailed) {
    return (
      <FamilyLoadFailure
        kicker="Paraphase"
        what={familyFailed ? 'Family' : 'Paraphase results'}
        error={familyFailed ? familyError : paraphaseError}
        notFoundMessage="This Paraphase workspace could not resolve the requested family."
        onRetry={() => {
          if (familyFailed) void refetchFamily();
          if (paraphaseFailed) void refetchParaphase();
        }}
      />
    );
  }

  if (!family || !paraphaseTable) {
    return (
      <PageState
        kicker="Paraphase"
        title="Family not found"
        message="This Paraphase workspace could not resolve the requested family."
      />
    );
  }

  return (
    <div className="page-shell family-paraphase-page space-y-6">
      <FamilyCaseHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="Paraphase"
        familyId={family.family_id}
        family={family}
        projectId={resolvedProjectId}
        assemblyLabel={referenceLabel}
        current="paraphase"
        facts={
          <>
            <div className="family-workspace-stat">
              <span className="stat-label">Clinical regions</span>
              <strong className="family-workspace-stat-value">
                {medicallyRelevantCount}
              </strong>
            </div>
            <div className="family-workspace-stat">
              <span className="stat-label">Sample calls</span>
              <strong className="family-workspace-stat-value">
                {sampleCallCount(paraphaseTable.genes)}
              </strong>
            </div>
          </>
        }
      />

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div className="space-y-1">
            <div className="paraphase-section-head">
              <h2 className="section-title">Duplicated-region results</h2>
              <InfoIcon
                name="About this list"
                text="Medically relevant Paraphase regions are shown by default. Show all to include exploratory regions."
              />
            </div>
          </div>
        </div>

        <div className="family-paraphase-toolbar">
          <label className="family-paraphase-filter-field" htmlFor="paraphase-gene-search">
            <span>Gene</span>
            <input
              id="paraphase-gene-search"
              type="search"
              value={searchText}
              onChange={(event) => setSearchText(event.target.value)}
              placeholder="Search gene"
            />
          </label>
          <label className="family-paraphase-filter-toggle">
            <input
              type="checkbox"
              checked={onlyCopyNumberSignals}
              onChange={(event) => setOnlyCopyNumberSignals(event.target.checked)}
            />
            <span>Copy-number changes and abnormal calls only</span>
          </label>
          <div className="family-paraphase-scope-toggle" aria-label="Region display scope">
            <button
              type="button"
              className={regionScope === 'clinical' ? 'is-active' : ''}
              onClick={() => setRegionScope('clinical')}
            >
              Only clinical
            </button>
            <button
              type="button"
              className={regionScope === 'all' ? 'is-active' : ''}
              onClick={() => setRegionScope('all')}
            >
              Show all
            </button>
          </div>
          <div className="family-paraphase-filter-count">
            {filteredGenes.length} of {paraphaseTable.genes.length} regions ·{' '}
            {medicallyRelevantCount} clinical · {copyNumberSignalCount} with CN signals
          </div>
        </div>

        {reviews.errorBanner}
        {filteredGenes.length === 0 ? (
          <p className="table-subtle">No Paraphase results match the current filters.</p>
        ) : (
          <div className="variant-card-list paraphase-card-list">
            {filteredGenes.map((gene) => (
              <ParaphaseLocusCard
                key={gene.gene_symbol}
                gene={gene}
                members={orderedMembers}
                familyId={family.family_id}
                projectId={resolvedProjectId}
                review={reviews.parts(gene.gene_symbol, gene.review, {
                  subject: gene.region_info?.display_name || gene.gene_symbol,
                  statuses: Object.values(gene.samples).map((call) => call.clinical_status),
                })}
              />
            ))}
          </div>
        )}
      </section>
      {reviews.dialog}
    </div>
  );
};

export default FamilyParaphasePage;
