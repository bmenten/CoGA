import React, { useMemo, useState } from 'react';
import { useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
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
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import { apiPath } from '../../lib/apiPath';
import InfoTip from '../../components/InfoTip';
import { COUNT_UNIT_MARK, countUnitWarningText, repeatStatusLabel } from '../../lib/repeatStatus';



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
    const normal = benignMin === benignMax ? `${benignMin}` : `${benignMin}-${benignMax}`;
    const abnormal =
      pathogenicMax != null && pathogenicMax !== pathogenicMin
        ? `${pathogenicMin}-${pathogenicMax}`
        : `${pathogenicMin}`;
    return `normal ${normal} · red ${abnormal}`;
  }

  // Each threshold the locus has, in order; a premutation range (FMR1) splits the grey zone
  // from the alleles that can expand. A locus with none reads Not assessed (CLIN-26).
  const thresholds = [
    warningMin != null ? `orange ≥ ${warningMin}` : null,
    row.premutation_min != null ? `premutation ≥ ${row.premutation_min}` : null,
    pathogenicMin != null ? `red ≥ ${pathogenicMin}` : null,
  ].filter((part): part is string => part !== null);
  return thresholds.length ? thresholds.join(' · ') : 'No reference cutoff';
};

const omimSearchHref = (disease: string): string =>
  `https://www.omim.org/search?index=entry&search=${encodeURIComponent(disease)}`;

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
  return `/families/${familyId}/chromosome/${chrom}?${params.toString()}`;
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
  params.set('back_path', `/families/${familyId}/repeat-expansions`);
  return `/families/${familyId}/igv?${params.toString()}`;
};

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
  // The members' calls at loci without thresholds, which the catalogue cannot classify
  // (CLIN-26): counted over the whole table, whatever the filters show.
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
                <span className="stat-label">Calls at loci without thresholds</span>
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
          <div className="space-y-1">
            <h2 className="section-title">TRGT repeat table</h2>
            <p className="catalog-card-copy">
              Grey marks normal loci, orange grey-zone alleles, deep orange a premutation (an FMR1 allele of 55–200 repeats, which
              can expand in a child), and red pathogenic expansions. Blue marks a call at a locus without thresholds: the
              catalogue gives no cutoff there, so the call is not assessed.
            </p>
            <p className="catalog-card-copy">
              {COUNT_UNIT_MARK} marks a count a few repeats from a boundary at a locus where QA has not yet validated that
              TRGT counts the clinical repeat number; there, a small difference could change the status. Hover over or
              focus it for the boundary. Aberrant only keeps such a locus too.
            </p>
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
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table family-repeat-table">
            <thead>
              <tr>
                <th>Repeat</th>
                <th>Disease</th>
                <th>Family calls</th>
                <th className="repeat-cutoff-head">Cutoffs</th>
              </tr>
            </thead>
            <tbody>
              {filteredLoci.map((row) => (
                <tr
                  key={`${row.locus_id}-${row.chr}-${row.start}`}
                  className={`family-repeat-table-row family-repeat-table-row--${row.status}${
                    isAbnormalStatus(row.status) ? ' family-repeat-table-row--abnormal' : ''
                  }`}
                >
                  <td>
                    <div className="family-repeat-table-locus-head">
                      <div className="family-repeat-locus-name">{row.display_name}</div>
                      {row.status !== 'normal' && (
                        <span
                          className={`table-chip family-repeat-table-locus-flag family-repeat-table-locus-flag--${row.status}`}
                        >
                          {repeatStatusLabel(row.status)}
                        </span>
                      )}
                    </div>
                    <div className="table-subtle">
                      chr{row.chr}:{row.start.toLocaleString()}-{row.end.toLocaleString()}
                    </div>
                    {row.motif && <div className="table-subtle">{row.motif}</div>}
                    {/* Both leave this list behind, so both open a tab — same as the
                        small-variant and structural-variant tables. */}
                    <div className="mt-1 inline-actions">
                      <GenomeWorkspaceLink
                        to={buildRepeatIgvHref(family.family_id, row, resolvedProjectId)}
                        className="variant-card-resource variant-card-resource--clinical"
                        label={`Open IGV at ${getRepeatIgvLocus(row)}`}
                      >
                        IGV
                      </GenomeWorkspaceLink>
                      <GenomeWorkspaceLink
                        to={buildRepeatChromosomeHref(
                          family.family_id,
                          row,
                          resolvedProjectId,
                        )}
                        className="variant-card-resource variant-card-resource--clinical"
                        label={`Chromosome view ${CHROMOSOME_VIEW_PADDING_LABEL} around ${getRepeatChromosomeViewWindow(row)}`}
                      >
                        Chromosome view
                      </GenomeWorkspaceLink>
                    </div>
                  </td>
                  <td>
                    {row.disease ? (
                      <a
                        href={omimSearchHref(row.disease)}
                        target="_blank"
                        rel="noreferrer"
                        className="table-link family-repeat-disease-link"
                        title={`Open ${row.disease} on OMIM`}
                      >
                        {row.disease}
                      </a>
                    ) : (
                      <span className="table-subtle">—</span>
                    )}
                  </td>
                  <td>
                    <div className="family-repeat-table-calls">
                      {orderedMembers.map((member) => {
                        const call = row.calls[member.sample_id];
                        return (
                          <div
                            key={member.sample_id}
                            className={`family-repeat-table-call-row${
                              call && isAbnormalStatus(call.status)
                                ? ' family-repeat-table-call-row--abnormal'
                                : ''
                            }`}
                          >
                            <div className="family-repeat-table-call-person">
                              <strong>{member.sample_id}</strong>
                              <div className="table-subtle">{member.role}</div>
                            </div>
                            <div className="family-repeat-table-call-value">
                              {call ? (
                                <div className="family-repeat-table-call-stack">
                                  <div
                                    className={`family-repeat-table-call-chip family-repeat-table-call-chip--${call.status}`}
                                  >
                                    <span className={`repeat-status repeat-status--${call.status}`}>
                                      {formatAlleles(call.alleles)}
                                    </span>
                                    {call.status !== 'normal' && (
                                      <span className="family-repeat-table-call-flag">
                                        {repeatStatusLabel(call.status)}
                                      </span>
                                    )}
                                  </div>
                                  {countUnitWarnings(call).length > 0 && (
                                    <InfoTip
                                      staticChild
                                      className="family-repeat-count-unit-flag"
                                      label={countUnitWarningText(countUnitWarnings(call), row.count_units_note)}
                                    >
                                      {COUNT_UNIT_MARK}
                                    </InfoTip>
                                  )}
                                  {formatAlleleMotifLabels(call.alleles).map((label) => (
                                    <div key={label} className="family-repeat-table-call-motifs">
                                      {label}
                                    </div>
                                  ))}
                                  {call.note && (
                                    <div className="family-repeat-table-call-motifs">{call.note}</div>
                                  )}
                                </div>
                              ) : (
                                <span className="table-subtle">No data</span>
                              )}
                            </div>
                          </div>
                        );
                      })}
                    </div>
                  </td>
                  <td className="repeat-cutoff-cell">
                    <div>{formatCutoff(row)}</div>
                  </td>
                </tr>
              ))}
              {filteredLoci.length === 0 && (
                <tr>
                  <td colSpan={4} className="table-subtle">
                    {repeatTable.loci.length
                      ? 'No repeat expansion loci match the selected filters.'
                      : 'No repeat expansion data is loaded for this family.'}
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default FamilyRepeatExpansionsPage;
