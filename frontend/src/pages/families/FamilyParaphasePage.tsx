import React, { useMemo, useState } from 'react';
import { Link, useLocation, useParams } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiFamilyParaphaseTable,
  ApiFamilyRecord,
  ApiParaphaseGeneResult,
  ApiParaphaseSampleResult,
} from '../../lib/apiTypes';
import Pedigree from '../../components/visualizations/Pedigree';
import PageState from '../../components/PageState';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';

interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

const parsePedigree = (pedigree?: string | null): PedRow[] => {
  if (!pedigree) return [];
  return pedigree
    .split('\n')
    .filter((line) => line.trim())
    .map((line) => {
      const [fid, iid, pid, mid, sex, phen] = line.trim().split(/\s+/);
      return { fid, iid, pid, mid, sex, phen };
    });
};

const formatNullableNumber = (value?: number | null, digits = 0): string => {
  if (value == null || Number.isNaN(value)) return 'n/a';
  return value.toLocaleString(undefined, {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  });
};

const hasCopyNumberSignal = (call?: ApiParaphaseSampleResult): boolean => {
  if (!call) return false;
  if (call.copy_number_signal) return true;
  if (call.copy_number_metrics?.some((metric) => metric.value == null || metric.value !== 2)) {
    return true;
  }
  return [call.total_cn, call.gene_cn, call.highest_total_cn].some(
    (value) => value != null && value !== 2,
  );
};

const formatMetricValue = (value?: number | null, digits = 0): string =>
  value == null ? 'no-call' : formatNullableNumber(value, digits);

const sampleCopyNumberMetrics = (call: ApiParaphaseSampleResult) =>
  call.copy_number_metrics?.length
    ? call.copy_number_metrics
    : [
        { key: 'total_cn', label: 'Total CN', value: call.total_cn },
        { key: 'gene_cn', label: 'Gene CN', value: call.gene_cn },
        { key: 'highest_total_cn', label: 'Max CN', value: call.highest_total_cn },
      ].filter((metric) => metric.value != null);

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

const FamilyParaphasePage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const [searchText, setSearchText] = useState('');
  const [onlyCopyNumberSignals, setOnlyCopyNumberSignals] = useState(false);
  const [regionScope, setRegionScope] = useState<'clinical' | 'all'>('clinical');
  const projectIdParam = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );

  const { data: family, isLoading: familyLoading } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const response = await api.get(`/families/${familyId}`);
      return response.data as ApiFamilyRecord;
    },
  });

  const {
    assemblyName,
    assemblyVersion,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
  } = useFamilyReference(family?.projects, projectIdParam);

  const { data: paraphaseTable, isLoading: paraphaseLoading } =
    useQuery<ApiFamilyParaphaseTable>({
      queryKey: ['family', familyId, 'paraphase', resolvedProjectId],
      queryFn: async () => {
        const response = await api.get(`/families/${familyId}/paraphase`, {
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
  const pedRows = useMemo(() => parsePedigree(family?.pedigree), [family?.pedigree]);
  const referenceLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion },
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
      return Boolean(gene.has_copy_number_signal) || Object.values(gene.samples).some((call) => hasCopyNumberSignal(call));
    });
  }, [onlyCopyNumberSignals, paraphaseTable?.genes, regionScope, searchText]);

  const copyNumberSignalCount = useMemo(
    () =>
      (paraphaseTable?.genes || []).filter((gene) =>
        Boolean(gene.has_copy_number_signal) || Object.values(gene.samples).some((call) => hasCopyNumberSignal(call)),
      ).length,
    [paraphaseTable?.genes],
  );
  const medicallyRelevantCount = useMemo(
    () => (paraphaseTable?.genes || []).filter((gene) => gene.is_medically_relevant).length,
    [paraphaseTable?.genes],
  );

  if (familyLoading || paraphaseLoading || (family?.projects?.length && referenceLoading)) {
    return (
      <PageState
        kicker="Paraphase"
        title="Loading Paraphase results"
        message="Preparing duplicated-region results and pedigree context."
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
      <section className="surface-card page-top-card">
        <div className={`page-top-card-grid${pedRows.length ? ' page-top-card-grid--with-visual' : ''}`}>
          <div className="page-top-card-copy">
            <div className="space-y-1">
              <p className="page-kicker">Paraphase</p>
              <h1 className="catalog-card-title">Family {family.family_id}</h1>
            </div>
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="stat-label">Members</span>
                <strong className="family-workspace-stat-value">{orderedMembers.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Assembly</span>
                <strong className="family-workspace-stat-copy">{referenceLabel}</strong>
              </div>
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
            </div>
          </div>
          {pedRows.length > 0 && (
            <div className="page-top-card-visual">
              <div className="page-top-card-pedigree family-workspace-pedigree">
                <p className="stat-label">Pedigree</p>
                <div className="mono-panel overflow-x-auto !bg-[rgba(255,255,255,0.92)]">
                  <Pedigree rows={pedRows} />
                </div>
              </div>
            </div>
          )}
        </div>
      </section>

      <section className="surface-card space-y-4">
        <div className="page-header">
          <div className="space-y-1">
            <h2 className="section-title">Duplicated-region results</h2>
            <p className="catalog-card-copy">
              Medically relevant Paraphase regions are shown by default. Show all to include exploratory regions.
            </p>
          </div>
          <Link to={`/families/${family.family_id}`} className="button-secondary hover:no-underline">
            Back to family workspace
          </Link>
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
            <span>Copy-number changes only</span>
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

        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table family-paraphase-table">
            <thead>
              <tr>
                <th>Gene</th>
                <th>Family copy number</th>
                <th>Sample results</th>
              </tr>
            </thead>
            <tbody>
              {filteredGenes.map((gene) => (
                <tr key={gene.gene_symbol}>
                  <td>
                    <div className="family-paraphase-gene-head">
                      <div>
                        <div className="font-semibold">
                          {gene.region_info?.display_name || gene.gene_symbol}
                        </div>
                        {gene.region_info?.display_name &&
                          gene.region_info.display_name !== gene.gene_symbol && (
                            <div className="table-subtle">{gene.gene_symbol}</div>
                          )}
                      </div>
                      {gene.is_medically_relevant && (
                        <span className="table-chip table-chip--accent">Clinical</span>
                      )}
                    </div>
                    {gene.region_info?.summary && (
                      <div className="paraphase-region-summary">{gene.region_info.summary}</div>
                    )}
                    {!!gene.region_info?.disorders?.length && (
                      <div className="paraphase-disorder-list">
                        {gene.region_info.disorders.map((disorder) => (
                          <a
                            key={disorder.name}
                            href={disorder.omim_url || '#'}
                            target="_blank"
                            rel="noreferrer"
                          >
                            {disorder.name}
                          </a>
                        ))}
                      </div>
                    )}
                    <div className="table-subtle">
                      Highest total CN {formatNullableNumber(gene.max_highest_total_cn)}
                    </div>
                  </td>
                  <td>
                    <div className="paraphase-cn-summary">
                      <span>
                        Total <strong>{formatNullableNumber(gene.max_total_cn)}</strong>
                      </span>
                      <span>
                        Gene <strong>{formatNullableNumber(gene.max_gene_cn)}</strong>
                      </span>
                    </div>
                  </td>
                  <td>
                    <div className="family-paraphase-calls">
                      {orderedMembers.map((member) => {
                        const call = gene.samples[member.sample_id];
                        const clinicalCnMetrics = call ? clinicalCopyNumberMetrics(gene, call) : [];
                        const otherCnMetrics = call
                          ? gene.is_medically_relevant
                            ? nonClinicalCopyNumberMetrics(gene, call)
                            : sampleCopyNumberMetrics(call)
                          : [];
                        const highlightedReadMetrics = call ? clinicalReadMetrics(gene, call) : [];
                        const highlightedReadKeys = new Set(
                          gene.region_info?.key_read_fields || [],
                        );
                        const secondaryReadMetrics =
                          call?.read_metrics?.filter((metric) => !highlightedReadKeys.has(metric.key)) || [];
                        return (
                          <div
                            key={member.sample_id}
                            className={`family-paraphase-call${
                              hasCopyNumberSignal(call) ? ' family-paraphase-call--signal' : ''
                            }`}
                          >
                            <div className="family-paraphase-call-head">
                              <strong>{member.sample_id}</strong>
                              <span className="table-subtle">{member.role}</span>
                            </div>
                            {call ? (
                              <>
                                {gene.is_medically_relevant && clinicalCnMetrics.length > 0 && (
                                  <div className="paraphase-clinical-metrics">
                                    {clinicalCnMetrics.map((metric) => (
                                      <span key={metric.key}>
                                        {metric.label}{' '}
                                        <strong>{formatMetricValue(metric.value)}</strong>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {otherCnMetrics.length > 0 && (
                                  <div className="paraphase-cn-grid">
                                    {otherCnMetrics.map((metric) => (
                                      <span key={metric.key}>
                                        {metric.label}{' '}
                                        <strong>{formatMetricValue(metric.value)}</strong>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {gene.is_medically_relevant && highlightedReadMetrics.length > 0 && (
                                  <div className="paraphase-read-grid paraphase-read-grid--clinical">
                                    {highlightedReadMetrics.map((metric) => (
                                      <span key={metric.key}>
                                        {metric.label}{' '}
                                        <strong>{formatMetricValue(metric.value)}</strong>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {secondaryReadMetrics.length > 0 && (
                                  <div className="paraphase-read-grid">
                                    {secondaryReadMetrics.map((metric) => (
                                      <span key={metric.key}>
                                        {metric.label}{' '}
                                        <strong>{formatMetricValue(metric.value)}</strong>
                                      </span>
                                    ))}
                                  </div>
                                )}
                                {!!gene.region_info?.notes?.length && hasClinicalNoCall(gene, call) && (
                                  <div className="paraphase-clinical-note">
                                    {gene.region_info.notes[0]}
                                  </div>
                                )}
                                {gene.is_medically_relevant && !!call.extra_fields?.length && (
                                  <div className="paraphase-extra-fields">
                                    {call.extra_fields.map((field) => (
                                      <div key={field.key} className="paraphase-extra-field">
                                        <div>
                                          <span>{field.label}</span>
                                          {field.description && <p>{field.description}</p>}
                                        </div>
                                        <strong>{formatExtraFieldValue(field.value)}</strong>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                <div className="table-subtle">
                                  {call.final_haplotype_count} final haplotypes ·{' '}
                                  {call.assembled_haplotype_count} assembled ·{' '}
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
                                          {group.haplotypes.length > 6
                                            ? ` +${group.haplotypes.length - 6} more`
                                            : ''}
                                        </div>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                <div className="table-subtle">
                                  Depth {formatRegionDepth(call.region_depth)} · genome{' '}
                                  {formatNullableNumber(call.genome_depth, 1)}
                                </div>
                                {call.phase_region && (
                                  <div className="table-subtle">{call.phase_region}</div>
                                )}
                              </>
                            ) : (
                              <span className="table-subtle">No data</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  </td>
                </tr>
              ))}
              {filteredGenes.length === 0 && (
                <tr>
                  <td colSpan={3} className="table-subtle">
                    No Paraphase results match the current filters.
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

export default FamilyParaphasePage;
