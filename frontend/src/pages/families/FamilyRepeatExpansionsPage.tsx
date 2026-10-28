import React, { useMemo, useState } from 'react';
import { useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiFamilyMember,
  ApiFamilyRecord,
  ApiFamilyRepeatExpansionTable,
  ApiRepeatExpansionAllele,
  ApiRepeatExpansionRow,
  ApiRepeatExpansionSampleCall,
  ApiRepeatStatus,
} from '../../lib/apiTypes';
import type { RepeatCountUnitWarningOut } from '../../lib/apiSchema.generated';
import FamilyPageHeader from './FamilyPageHeader';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import { apiPath } from '../../lib/apiPath';
import InfoTip from '../../components/InfoTip';
import GeneDiseaseHeading from '../../components/GeneDiseaseHeading';
import TruncatedText from '../../components/TruncatedText';
import { omimGeneHref, omimSearchHref } from '../../lib/omim';
import { geneInfoHref } from '../../lib/geneInfo';
import InfoIcon from '../../components/InfoIcon';
import FindingCard, { FindingLinks, type FindingRail } from './FindingCard';
import FamilyGenotypeGrid, { STATUS_CHIP_TONE_CLASS, type GenotypeTone } from './FamilyGenotypeGrid';
import { COUNT_UNIT_MARK, countUnitWarningText, repeatStatusLabel } from '../../lib/repeatStatus';
import { raw, routePath } from '../../lib/routePath';



// A catalogued range of counts: "10" for a single count, "6-26" otherwise.
const formatCountRange = (min: number, max: number): string => (min === max ? `${min}` : `${min}-${max}`);

/**
 * How the catalog defines abnormal at this locus.
 *
 * Almost every locus is pathogenic by expansion, where a lower bound says it all. A
 * couple are pathogenic by *contraction* — VWA1 is normal at exactly 2 copies and
 * abnormal at 1 or 3 — and there "red ≥ 1" reads backwards, marking every healthy call.
 * Those are recognised the same way the classifier does it: a benign count sitting at or
 * above the pathogenic threshold means count and risk do not rise together.
 */
export const formatCutoff = (row: {
  warning_min?: number | null;
  pathogenic_min?: number | null;
  benign_min?: number | null;
  benign_max?: number | null;
  pathogenic_max?: number | null;
  premutation_min?: number | null;
}): string => {
  const { warning_min: warningMin, pathogenic_min: pathogenicMin } = row;
  const { benign_min: benignMin, benign_max: benignMax, pathogenic_max: pathogenicMax } = row;
  const hasBenignRange = benignMin != null && benignMax != null;

  if (hasBenignRange && pathogenicMin != null && benignMax >= pathogenicMin) {
    const abnormal =
      pathogenicMax != null && pathogenicMax !== pathogenicMin
        ? `${pathogenicMin}-${pathogenicMax}`
        : `${pathogenicMin}`;
    return `normal ${formatCountRange(benignMin, benignMax)} · red ${abnormal}`;
  }

  // Each threshold the locus has, in order; a premutation range (FMR1) splits the grey zone
  // from the alleles that can expand.
  const thresholds = [
    warningMin != null ? `orange ≥ ${warningMin}` : null,
    row.premutation_min != null ? `premutation ≥ ${row.premutation_min}` : null,
    pathogenicMin != null ? `red ≥ ${pathogenicMin}` : null,
  ].filter((part): part is string => part !== null);
  if (thresholds.length) return thresholds.join(' · ');
  // A locus without thresholds: a count inside the benign range the catalogue states there
  // reads Normal (POLG 10, CLIN-36), any other count Not assessed (CLIN-26).
  if (hasBenignRange) return `normal ${formatCountRange(benignMin, benignMax)} · no threshold`;
  return 'No reference cutoff';
};

// A repeat motif or sequence longer than this shows its first nucleotides and "…" on screen,
// the whole of it in its tooltip.
export const MOTIF_DISPLAY_MAX = 10;

// The disease after the gene in the locus heading, with its OMIM search. A locus outside the
// catalogue carries its own ID as its disease (chr2_1000000_1000030_CAG): that names no
// disease, and the heading already shows it.
const locusDiseases = (row: ApiRepeatExpansionRow) => {
  const disease = (row.disease || '').trim();
  if (!disease || [row.display_name, row.gene, row.locus_id].includes(disease)) return [];
  return [{ name: disease, omimUrl: omimSearchHref(disease) }];
};

const formatAlleles = (alleles: ApiRepeatExpansionAllele[]): string => {
  const values = alleles
    .map((allele) => allele.repeat_count)
    .filter((value): value is number => value != null);
  return values.length ? values.join(' / ') : 'No call';
};

const formatAlleleMotifLabels = (alleles: ApiRepeatExpansionAllele[]): string[] =>
  alleles
    .map((allele, index) => {
      if (!allele.interruption_label) return null;
      const label = allele.interrupted ? 'Interruption' : 'Motifs';
      return `${label} A${index + 1}: ${allele.interruption_label}`;
    })
    .filter((value): value is string => Boolean(value));

// The statuses the aberrant-only view keeps: every one but normal and a no-call.
const isAbnormalStatus = (status: ApiRepeatStatus): boolean =>
  status === 'review' ||
  status === 'not_assessed' ||
  status === 'intermediate' ||
  status === 'premutation' ||
  status === 'pathogenic';

// A call's counts near a boundary at a locus whose TRGT counting units QA has not validated:
// a difference of a few repeats between TRGT's count and the clinical repeat number could
// change their status (repeat_count_units.py). None once the locus is validated.
const countUnitWarnings = (call: ApiRepeatExpansionSampleCall): RepeatCountUnitWarningOut[] =>
  call.alleles
    .map((allele) => allele.count_unit_warning)
    .filter((warning): warning is RepeatCountUnitWarningOut => Boolean(warning));

const hasCountUnitWarning = (row: ApiRepeatExpansionRow): boolean =>
  Object.values(row.calls).some((call) => countUnitWarnings(call).length > 0);

const CHROMOSOME_VIEW_PADDING_BP = 1_000_000;
const CHROMOSOME_VIEW_PADDING_LABEL = '±1 MB';

const getRepeatChromosomeViewWindow = (row: ApiRepeatExpansionRow) =>
  `chr${row.chr}:${Math.max(0, row.start - CHROMOSOME_VIEW_PADDING_BP).toLocaleString()}-${(row.end + CHROMOSOME_VIEW_PADDING_BP).toLocaleString()}`;

const buildRepeatChromosomeHref = (
  familyId: string,
  row: ApiRepeatExpansionRow,
  projectId?: string,
): string => {
  const chrom = row.chr.replace(/^chr/i, '');
  const params = new URLSearchParams({
    start: String(Math.max(0, row.start - CHROMOSOME_VIEW_PADDING_BP)),
    end: String(row.end + CHROMOSOME_VIEW_PADDING_BP),
  });
  if (projectId) {
    params.set('project_id', projectId);
  }
  return routePath`/families/${familyId}/chromosome/${chrom}${raw(`?${params.toString()}`)}`;
};

// A repeat locus is small, and what matters in IGV is the reads spanning it, so the
// window is the repeat plus enough flank to see them anchor — the same reasoning (and
// flank) as a structural variant. The 1 MB chromosome-view padding is for a different
// question: where the repeat sits on the chromosome.
const IGV_PADDING_BP = 1_000;

const getRepeatIgvLocus = (row: ApiRepeatExpansionRow) => {
  const chrom = row.chr.startsWith('chr') ? row.chr : `chr${row.chr}`;
  const end = Math.max(row.start, row.end);
  return `${chrom}:${Math.max(1, row.start - IGV_PADDING_BP)}-${end + IGV_PADDING_BP}`;
};

const buildRepeatIgvHref = (
  familyId: string,
  row: ApiRepeatExpansionRow,
  projectId?: string,
): string => {
  const params = new URLSearchParams({ locus: getRepeatIgvLocus(row) });
  if (projectId) {
    params.set('project_id', projectId);
  }
  params.set('back_path', routePath`/families/${familyId}/repeat-expansions`);
  return routePath`/families/${familyId}/igv${raw(`?${params.toString()}`)}`;
};

// How to read the calls: explanation, not this family's result, so it sits behind the info icon
// beside the list's title. Each call says its own status in words.
// The gene a locus names, for its gene page and OMIM: none for a locus outside the catalogue,
// whose gene is its own coordinate ID (chr2_1000000_1000030_CAG).
const UNCATALOGUED_LOCUS_ID = /^(chr)?[0-9XYMT]+_\d+_\d+/i;
const locusGene = (row: ApiRepeatExpansionRow): string | null =>
  row.gene && !UNCATALOGUED_LOCUS_ID.test(row.gene) ? row.gene : null;

const REPEAT_READING_GUIDE =
  'Each member\'s call gives the repeat count of both alleles and, when it is not normal, its status: ' +
  'grey zone or premutation (amber; a premutation is an FMR1 allele of 55–200 repeats, which can expand ' +
  'in a child), pathogenic (red), or not assessed (blue: the catalogue gives the locus no threshold and ' +
  'the count is outside any benign range it states there). ' +
  `${COUNT_UNIT_MARK} marks a count a few repeats from a boundary at a locus where QA has not yet ` +
  'validated that TRGT counts the clinical repeat number; there, a small difference could change the ' +
  'status. Hover over or focus it for the boundary. Aberrant only keeps such a locus too.';

// Each status's tone on the card (FamilyGenotypeGrid): the call, its status chip and the
// locus chip. A review call has a tone of its own, never another status's (ENG-10).
export const REPEAT_STATUS_TONE: Record<ApiRepeatStatus, GenotypeTone> = {
  normal: 'ref',
  intermediate: 'warn',
  premutation: 'warn',
  pathogenic: 'alert',
  not_assessed: 'info',
  review: 'review',
  unknown: 'na',
};

const repeatRail = (status: ApiRepeatStatus): FindingRail => {
  if (status === 'pathogenic') return 'pathogenic';
  if (status === 'intermediate' || status === 'premutation') return 'warning';
  return 'neutral';
};

// One repeat locus as a variant card: the locus and its catalogue cutoffs at the left, each
// member's alleles in the genotype grid, the genome and OMIM links in the footer.
const RepeatLocusCard: React.FC<{
  row: ApiRepeatExpansionRow;
  members: ApiFamilyMember[];
  familyId: string;
  projectId?: string;
}> = ({ row, members, familyId, projectId }) => (
  <FindingCard
    aria-label={`${row.display_name} repeat`}
    rail={repeatRail(row.status)}
    className={`family-repeat-card family-repeat-card--${row.status}`}
    heading={
      <GeneDiseaseHeading
        gene={row.display_name}
        geneHref={geneInfoHref(locusGene(row), familyId, projectId)}
        diseases={locusDiseases(row)}
      >
        {row.inheritance ? (
          <span className="variant-card-chip variant-card-chip--soft">{row.inheritance}</span>
        ) : null}
      </GeneDiseaseHeading>
    }
    tags={
      row.status !== 'normal' ? (
        <span
          className={`variant-card-chip variant-card-chip--${STATUS_CHIP_TONE_CLASS[REPEAT_STATUS_TONE[row.status]]}`}
        >
          {repeatStatusLabel(row.status, row.review_reason)}
        </span>
      ) : null
    }
    columns={[
      {
        key: 'variant',
        title: 'Repeat',
        content: (
          <dl className="variant-card-mini-dl">
            <div>
              <dt>Locus</dt>
              <dd>
                chr{row.chr}:{row.start.toLocaleString()}-{row.end.toLocaleString()}
              </dd>
            </div>
            <div>
              <dt>Motif</dt>
              <dd>{row.motif ? <TruncatedText text={row.motif} max={MOTIF_DISPLAY_MAX} mono /> : '—'}</dd>
            </div>
            <div>
              <dt>Cutoffs</dt>
              <dd className="repeat-cutoff-cell">{formatCutoff(row)}</dd>
            </div>
          </dl>
        ),
      },
      {
        key: 'genotypes',
        title: 'Genotypes',
        content: (
          <FamilyGenotypeGrid
            callLabel="Alleles"
            withStatus
            entries={members.map((member) => {
              const call = row.calls[member.sample_id];
              if (!call) {
                return { member, tone: 'na' as const, call: 'No data' };
              }
              const warnings = countUnitWarnings(call);
              const details = [...formatAlleleMotifLabels(call.alleles), ...(call.note ? [call.note] : [])];
              return {
                member,
                tone: REPEAT_STATUS_TONE[call.status],
                call: (
                  <span className="family-repeat-alleles">
                    {formatAlleles(call.alleles)}
                    {warnings.length > 0 && (
                      <InfoTip
                        staticChild
                        className="family-repeat-count-unit-flag"
                        label={countUnitWarningText(warnings, row.count_units_note)}
                      >
                        {COUNT_UNIT_MARK}
                      </InfoTip>
                    )}
                  </span>
                ),
                status:
                  call.status !== 'normal'
                    ? {
                        label: repeatStatusLabel(call.status, call.review_reason),
                        tone: REPEAT_STATUS_TONE[call.status],
                      }
                    : null,
                detail: details.length ? details.join(' · ') : undefined,
              };
            })}
          />
        ),
      },
    ]}
    links={
      <FindingLinks
        igv={{ href: buildRepeatIgvHref(familyId, row, projectId), label: `Open IGV at ${getRepeatIgvLocus(row)}` }}
        chromosome={{
          href: buildRepeatChromosomeHref(familyId, row, projectId),
          label: `Chromosome view ${CHROMOSOME_VIEW_PADDING_LABEL} around ${getRepeatChromosomeViewWindow(row)}`,
        }}
        omim={locusGene(row) ? omimGeneHref(locusGene(row) as string) : null}
      />
    }
  />
);

const FamilyRepeatExpansionsPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const projectIdParam = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const [geneFilter, setGeneFilter] = useState('');
  const [diseaseFilter, setDiseaseFilter] = useState('');
  const [aberrantOnly, setAberrantOnly] = useState(false);

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
    assemblyVersion,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectIdParam);

  const {
    data: repeatTable,
    isLoading: repeatLoading,
    isError: repeatFailed,
    error: repeatError,
    refetch: refetchRepeats,
  } = useQuery<ApiFamilyRepeatExpansionTable>({
    queryKey: ['family', familyId, 'repeat-expansions', resolvedProjectId],
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}/repeat-expansions`, {
        params: resolvedProjectId ? { project_id: resolvedProjectId } : undefined,
      });
      return response.data as ApiFamilyRepeatExpansionTable;
    },
    enabled: Boolean(familyId && (!family?.projects?.length || !referenceLoading)),
  });

  const orderedMembers = useMemo(
    () => sortFamilyMembersProbandFirst(repeatTable?.samples || family?.members || []),
    [family?.members, repeatTable?.samples],
  );
  const geneOptions = useMemo(
    () =>
      Array.from(new Set((repeatTable?.loci || []).map((row) => row.gene).filter(Boolean))).sort(
        (left, right) => left.localeCompare(right),
      ),
    [repeatTable?.loci],
  );
  const diseaseOptions = useMemo(
    () =>
      Array.from(
        new Set((repeatTable?.loci || []).map((row) => row.disease).filter(Boolean)),
      ).sort((left, right) => left.localeCompare(right)),
    [repeatTable?.loci],
  );
  const filteredLoci = useMemo(
    () =>
      (repeatTable?.loci || []).filter((row) => {
        if (geneFilter && row.gene !== geneFilter) return false;
        if (diseaseFilter && row.disease !== diseaseFilter) return false;
        // A count near a boundary may read another status in the clinical repeat number, so
        // its locus stays in view whatever its status.
        if (aberrantOnly && !isAbnormalStatus(row.status) && !hasCountUnitWarning(row)) return false;
        return true;
      }),
    [aberrantOnly, diseaseFilter, geneFilter, repeatTable?.loci],
  );
  // The members' calls not assessed: at a locus without thresholds, outside any benign range
  // the catalogue states there (CLIN-26, CLIN-36). Counted over the whole table, whatever the
  // filters show.
  const notAssessedCalls = useMemo(
    () =>
      (repeatTable?.loci || []).reduce(
        (total, row) =>
          total + Object.values(row.calls).filter((call) => call.status === 'not_assessed').length,
        0,
      ),
    [repeatTable?.loci],
  );
  // The members' calls with a count near a boundary while TRGT's counting units are not
  // validated, counted over the whole table, whatever the filters show.
  const nearBoundaryCalls = useMemo(
    () =>
      (repeatTable?.loci || []).reduce(
        (total, row) =>
          total + Object.values(row.calls).filter((call) => countUnitWarnings(call).length > 0).length,
        0,
      ),
    [repeatTable?.loci],
  );
  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion, isError: referenceFailed },
    'Not linked',
  );

  if (familyLoading || repeatLoading || (family?.projects?.length && referenceLoading)) {
    return (
      <PageState
        loading
        kicker="Repeats"
        title="Loading repeat expansions"
        message="Preparing the family repeat expansion table and pedigree context."
      />
    );
  }

  if (familyFailed || repeatFailed) {
    return (
      <FamilyLoadFailure
        kicker="Repeats"
        what={familyFailed ? 'Family' : 'Repeat expansions'}
        error={familyFailed ? familyError : repeatError}
        notFoundMessage="This repeat expansion workspace could not resolve the requested family."
        onRetry={() => {
          if (familyFailed) void refetchFamily();
          if (repeatFailed) void refetchRepeats();
        }}
      />
    );
  }

  if (!family || !repeatTable) {
    return (
      <PageState
        kicker="Repeats"
        title="Family not found"
        message="This repeat expansion workspace could not resolve the requested family."
      />
    );
  }

  return (
    <div className="page-shell family-repeat-page space-y-6">
      <FamilyPageHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="Repeat expansions"
        family={family}
        projectId={resolvedProjectId}
      >
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="stat-label">Members</span>
                <strong className="family-workspace-stat-value">{orderedMembers.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Assembly</span>
                <strong className="family-workspace-stat-copy">
                  {referenceLabel}
                </strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Loci with calls</span>
                <strong className="family-workspace-stat-value">{repeatTable.loci.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Calls not assessed</span>
                <strong className="family-workspace-stat-value">{notAssessedCalls}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Calls near a boundary</span>
                <strong className="family-workspace-stat-value">{nearBoundaryCalls}</strong>
              </div>
            </div>
      </FamilyPageHeader>

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div className="paraphase-section-head">
            <h2 className="section-title">TRGT repeat expansions</h2>
            <InfoIcon name="How to read the repeat calls" text={REPEAT_READING_GUIDE} />
          </div>
        </div>
        <div className="family-repeat-toolbar">
          <label className="family-repeat-filter-field">
            <span>Gene</span>
            <select value={geneFilter} onChange={(event) => setGeneFilter(event.target.value)}>
              <option value="">All genes</option>
              {geneOptions.map((gene) => (
                <option key={gene} value={gene}>
                  {gene}
                </option>
              ))}
            </select>
          </label>
          <label className="family-repeat-filter-field">
            <span>Disease</span>
            <select
              value={diseaseFilter}
              onChange={(event) => setDiseaseFilter(event.target.value)}
            >
              <option value="">All diseases</option>
              {diseaseOptions.map((disease) => (
                <option key={disease} value={disease}>
                  {disease}
                </option>
              ))}
            </select>
          </label>
          <label className="family-repeat-filter-toggle">
            <input
              type="checkbox"
              checked={aberrantOnly}
              onChange={(event) => setAberrantOnly(event.target.checked)}
            />
            <span>Aberrant only</span>
          </label>
          <div className="family-repeat-filter-count">
            {filteredLoci.length} of {repeatTable.loci.length} loci
          </div>
        </div>
        <div className="variant-card-list family-repeat-list">
          {filteredLoci.map((row) => (
            <RepeatLocusCard
              key={`${row.locus_id}-${row.chr}-${row.start}`}
              row={row}
              members={orderedMembers}
              familyId={family.family_id}
              projectId={resolvedProjectId}
            />
          ))}
          {filteredLoci.length === 0 && (
            <p className="table-subtle">
              {repeatTable.loci.length
                ? 'No repeat expansion loci match the selected filters.'
                : 'No repeat expansion data is loaded for this family.'}
            </p>
          )}
        </div>
      </section>
    </div>
  );
};

export default FamilyRepeatExpansionsPage;
