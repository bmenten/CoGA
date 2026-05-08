import React, { useEffect, useMemo, useState } from 'react';
import { useParams, Link, useLocation } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import type {
  ApiFamilyRecord,
  ApiFamilyRegionOfInterest,
  ApiPaginatedTotalResponse,
  ApiSmallVariantReviewSummary,
} from '../../lib/apiTypes';
import Pedigree from '../../components/visualizations/Pedigree';
import PageState from '../../components/PageState';
import { isAdmin } from '../../lib/auth';
import { getErrorMessage } from '../../lib/errorMessage';
import { sortFamilyMembersProbandFirst } from '../../lib/familyMembers';
import {
  formatResolvedReferenceLabel,
  useFamilyReference,
  useProjectCatalog,
} from '../../lib/reference';
import {
  getTagDefinitionMap,
  sortTagDefinitions,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { getReviewTagStyle } from './smallVariantResultUtils';

interface PedRow {
  fid: string;
  iid: string;
  pid: string;
  mid: string;
  sex: string;
  phen: string;
}

const phenotypeLabel = (phenotype?: string | null, affected?: boolean): string => {
  if (phenotype === '2') return 'affected';
  if (phenotype === '1') return 'unaffected';
  if (phenotype === '0' || phenotype === '-9') return 'unknown';
  if (affected === true) return 'affected';
  if (affected === false) return 'unaffected';
  return 'unknown';
};

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

const formatRegion = (roi: ApiFamilyRegionOfInterest): string => {
  const chrom = roi.chr.startsWith('chr') ? roi.chr : `chr${roi.chr}`;
  return `${chrom}:${roi.start.toLocaleString()}-${roi.end.toLocaleString()}`;
};

const getReviewSummaryTags = (
  tagCounts: Record<string, number> | undefined,
  tagDefinitions: SmallVariantTagDefinition[],
) => {
  const counts = tagCounts || {};
  const activeKeys = new Set<string>();
  const knownTags = sortTagDefinitions(tagDefinitions)
    .map((tag) => {
      const count = counts[tag.key] ?? 0;
      if (count <= 0) return null;
      activeKeys.add(tag.key);
      return {
        key: tag.key,
        label: tag.label,
        count,
      };
    })
    .filter((entry): entry is { key: string; label: string; count: number } => entry !== null);
  const unknownTags = Object.entries(counts)
    .filter(([key, count]) => count > 0 && !activeKeys.has(key))
    .sort(([left], [right]) => left.localeCompare(right, undefined, { sensitivity: 'base' }))
    .map(([key, count]) => ({
      key,
      label: key,
      count,
    }));
  return [...knownTags, ...unknownTags];
};

const FamilyDetailPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const preferredProjectId = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const userIsAdmin = isAdmin();
  const queryClient = useQueryClient();
  const [roiInput, setRoiInput] = useState('');
  const [roiBusy, setRoiBusy] = useState(false);
  const [roiStatus, setRoiStatus] = useState<{ tone: 'success' | 'error'; message: string } | null>(
    null,
  );

  const { data, isLoading } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}`);
      return res.data as ApiFamilyRecord;
    },
  });

  const { data: variantPage } = useQuery<ApiPaginatedTotalResponse>({
    queryKey: ['family', familyId, 'structural-variants', 'has-data'],
    queryFn: async () => {
      const params = new URLSearchParams({ page: '1', page_size: '1' });
      const res = await api.get(`/families/${familyId}/structural-variants?${params.toString()}`);
      return res.data as ApiPaginatedTotalResponse;
    },
  });

  const { data: familyVariantPage } = useQuery<ApiPaginatedTotalResponse>({
    queryKey: ['family', familyId, 'small-variants', 'has-data'],
    queryFn: async () => {
      const params = new URLSearchParams({ page: '1', page_size: '1' });
      const res = await api.get(`/families/${familyId}/small-variants?${params.toString()}`);
      return res.data as ApiPaginatedTotalResponse;
    },
  });

  const hasVariants = (variantPage?.total ?? 0) > 0;
  const hasSmallVariants = (familyVariantPage?.total ?? 0) > 0;
  const variantCountsLoaded = variantPage !== undefined && familyVariantPage !== undefined;
  const {
    assemblyName,
    assemblyVersion,
    projectId,
    isLoading: referenceLoading,
  } = useFamilyReference(data?.projects, preferredProjectId);
  const { data: projects = [] } = useProjectCatalog();
  const assemblyLabel = formatResolvedReferenceLabel(
    { assemblyName, assemblyVersion },
    data?.projects?.length && referenceLoading ? 'Loading linked reference...' : 'Not linked',
  );
  const { data: reviewSummary } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'small-variant-review-summary'],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: smallVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'small-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasSmallVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/small-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });
  const { data: structuralReviewSummary } = useQuery<ApiSmallVariantReviewSummary>({
    queryKey: ['family', familyId, 'structural-variant-review-summary'],
    enabled: Boolean(familyId && hasVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-review-summary`);
      return res.data as ApiSmallVariantReviewSummary;
    },
  });
  const { data: structuralVariantTags = [] } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['family', familyId, 'structural-variant-tags', 'summary', projectId || null],
    enabled: Boolean(familyId && hasVariants),
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-tags`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as SmallVariantTagDefinition[];
    },
  });

  const familyProjects = useMemo(() => {
    if (!data?.projects?.length) return [];
    const projectNameById = new Map(projects.map((project) => [project.id, project.name]));
    return data.projects.map((projectIdValue) => ({
      id: projectIdValue,
      name: projectNameById.get(projectIdValue) ?? projectIdValue,
    }));
  }, [data?.projects, projects]);
  const smallVariantReviewTagMap = useMemo(() => getTagDefinitionMap(smallVariantTags), [smallVariantTags]);
  const structuralVariantReviewTagMap = useMemo(
    () => getTagDefinitionMap(structuralVariantTags),
    [structuralVariantTags],
  );
  const reviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(reviewSummary?.tag_counts, smallVariantTags);
  }, [reviewSummary?.tag_counts, smallVariantTags]);
  const structuralReviewSummaryTags = useMemo(() => {
    return getReviewSummaryTags(structuralReviewSummary?.tag_counts, structuralVariantTags);
  }, [structuralReviewSummary?.tag_counts, structuralVariantTags]);

  useEffect(() => {
    setRoiInput(data?.roi?.query ?? '');
  }, [data?.roi?.query]);

  if (isLoading) {
    return (
      <PageState
        kicker="Family"
        title="Loading family workspace"
        message="Preparing family metadata, pedigree and navigation links."
      />
    );
  }

  if (!data) {
    return (
      <PageState
        kicker="Family"
        title="Family not found"
        message="This workspace could not resolve the requested family."
      />
    );
  }

  const pedRows = parsePedigree(data.pedigree);
  const orderedMembers = sortFamilyMembersProbandFirst(data.members);

  const saveRoi = async (clear = false) => {
    if (!familyId) return;
    setRoiBusy(true);
    setRoiStatus(null);
    try {
      const response = await api.put(`/families/${familyId}/roi`, {
        query: clear ? '' : roiInput,
        project_id: projectId,
      });
      const updatedFamily = response.data as ApiFamilyRecord;
      queryClient.setQueryData(['family', familyId], updatedFamily);
      await queryClient.invalidateQueries({ queryKey: ['families'] });
      setRoiInput(updatedFamily.roi?.query ?? '');
      setRoiStatus({
        tone: 'success',
        message: clear
          ? 'Family ROI cleared.'
          : `Family ROI saved for ${updatedFamily.roi?.label ?? 'the selected locus'}.`,
      });
    } catch (error) {
      setRoiStatus({
        tone: 'error',
        message: getErrorMessage(error, 'Failed to update the family ROI.'),
      });
    } finally {
      setRoiBusy(false);
    }
  };

  return (
    <div className="page-shell family-detail-page space-y-6">
      <section className="surface-card page-top-card">
        <div className={`page-top-card-grid${pedRows.length ? ' page-top-card-grid--with-visual' : ''}`}>
          <div className="page-top-card-copy family-workspace-copy">
            <div className="space-y-1">
              <p className="page-kicker">Family Workspace</p>
              <h1 className="catalog-card-title">Family {data.family_id}</h1>
            </div>
            <div className="family-workspace-summary">
              <div className="family-workspace-stat">
                <span className="stat-label">Members</span>
                <strong className="family-workspace-stat-value">{orderedMembers.length}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Assembly</span>
                <strong className="family-workspace-stat-copy">{assemblyLabel}</strong>
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">ROI</span>
                <strong className="family-workspace-stat-copy">
                  {data.roi ? data.roi.label : 'Not set'}
                </strong>
                {data.roi && (
                  <span className="family-workspace-stat-note">{formatRegion(data.roi)}</span>
                )}
              </div>
              <div className="family-workspace-stat">
                <span className="stat-label">Projects</span>
                {familyProjects.length > 0 ? (
                  <div className="family-workspace-projects">
                    {familyProjects.map((project) => (
                      <span key={project.id} className="table-chip">
                        {project.name}
                      </span>
                    ))}
                  </div>
                ) : (
                  <strong className="family-workspace-stat-copy">Not linked</strong>
                )}
              </div>
            </div>
            {hasSmallVariants || hasVariants ? (
              <div className="family-review-summary" aria-label="Variant curation summary">
                <span className="family-review-summary-label">Curation</span>
                {hasSmallVariants ? (
                  <div className="family-review-summary-group" aria-label="Small variant review summary">
                    <span className="table-chip family-review-summary-chip family-review-summary-chip--scope">
                      Small variants
                    </span>
                    <span className="table-chip family-review-summary-chip">
                      Reviewed <strong>{reviewSummary?.reviewed_variant_count ?? 0}</strong>
                    </span>
                    <span className="table-chip family-review-summary-chip family-review-summary-chip--notes">
                      Notes <strong>{reviewSummary?.note_count ?? 0}</strong>
                    </span>
                    {reviewSummaryTags.map((tag) => (
                      <span
                        key={tag.key}
                        className="table-chip table-chip--tag family-review-summary-chip"
                        style={getReviewTagStyle(tag.key, smallVariantReviewTagMap)}
                        title={`${tag.count} small variants tagged ${tag.label}`}
                      >
                        {tag.label} <strong>{tag.count}</strong>
                      </span>
                    ))}
                  </div>
                ) : null}
                {hasVariants ? (
                  <div className="family-review-summary-group" aria-label="Structural variant review summary">
                    <span className="table-chip family-review-summary-chip family-review-summary-chip--scope">
                      Structural variants
                    </span>
                    <span className="table-chip family-review-summary-chip">
                      Reviewed <strong>{structuralReviewSummary?.reviewed_variant_count ?? 0}</strong>
                    </span>
                    <span className="table-chip family-review-summary-chip family-review-summary-chip--notes">
                      Notes <strong>{structuralReviewSummary?.note_count ?? 0}</strong>
                    </span>
                    {structuralReviewSummaryTags.map((tag) => (
                      <span
                        key={tag.key}
                        className="table-chip table-chip--tag family-review-summary-chip"
                        style={getReviewTagStyle(tag.key, structuralVariantReviewTagMap)}
                        title={`${tag.count} structural variants tagged ${tag.label}`}
                      >
                        {tag.label} <strong>{tag.count}</strong>
                      </span>
                    ))}
                  </div>
                ) : null}
              </div>
            ) : null}
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

      <section className="family-workspace-grid">
        <article className="surface-card-flat family-workspace-card">
          <div className="family-workspace-card-head">
            <h2 className="section-title">Variants</h2>
          </div>
          <div className="compact-toolbar family-toolbar">
            <Link
              to={`/families/${data.family_id}/structural-variants`}
              className="button-secondary hover:no-underline"
            >
              Structural variants
            </Link>
            <Link
              to={`/families/${data.family_id}/small-variants`}
              className="button-secondary hover:no-underline"
            >
              Small variants
            </Link>
            <Link
              to={`/families/${data.family_id}/variant-summary`}
              className="button-secondary hover:no-underline"
            >
              Variant summary
            </Link>
            <Link
              to={`/families/${data.family_id}/repeat-expansions`}
              className="button-secondary hover:no-underline"
            >
              Repeat expansions
            </Link>
            <Link
              to={`/families/${data.family_id}/paraphase`}
              className="button-secondary hover:no-underline"
            >
              Paraphase
            </Link>
          </div>
          {variantCountsLoaded && !hasVariants && !hasSmallVariants && (
            <p className="dashboard-link-note">No family variant data is loaded yet.</p>
          )}
        </article>

        <article className="surface-card-flat family-workspace-card">
          <div className="family-workspace-card-head">
            <h2 className="section-title">Visualization</h2>
          </div>
          <div className="compact-toolbar family-toolbar">
            <Link
              to={`/families/${data.family_id}/genome`}
              className="button-secondary hover:no-underline"
            >
              Genome view
            </Link>
            <Link
              to={`/families/${data.family_id}/chromosome/1`}
              className="button-secondary hover:no-underline"
            >
              Chromosome view
            </Link>
            <Link
              to={`/families/${data.family_id}/circos`}
              className="button-secondary hover:no-underline"
            >
              Circos plot
            </Link>
            <Link
              to={`/families/${data.family_id}/igv`}
              className="button-secondary hover:no-underline"
            >
              IGV viewer
            </Link>
          </div>
        </article>

        <article className="surface-card-flat family-workspace-card">
          <div className="family-workspace-card-head">
            <h2 className="section-title">ROI</h2>
          </div>
          {data.roi && (
            <div className="compact-toolbar family-toolbar">
              <Link
                to={`/families/${data.family_id}/chromosome/${data.roi.chr}?start=${data.roi.start}&end=${data.roi.end}${
                  projectId ? `&project_id=${projectId}` : ''
                }`}
                className="button-secondary hover:no-underline"
              >
                Open ROI in chromosome view
              </Link>
            </div>
          )}
          {!data.roi && !userIsAdmin && (
            <p className="dashboard-link-note">No family region of interest is defined.</p>
          )}
          {userIsAdmin && (
            <div className="family-roi-admin">
              <label className="field-label family-roi-input">
                Gene symbol or genomic locus
                <input
                  type="text"
                  value={roiInput}
                  onChange={(e) => setRoiInput(e.target.value)}
                  placeholder="BRCA1 or chr17:43044295-43125482"
                  disabled={roiBusy}
                />
              </label>
              <div className="compact-toolbar family-toolbar">
                <button
                  type="button"
                  onClick={() => saveRoi(false)}
                  disabled={roiBusy || roiInput.trim().length === 0}
                >
                  Save ROI
                </button>
                <button
                  type="button"
                  className="button-ghost"
                  onClick={() => saveRoi(true)}
                  disabled={roiBusy || !data.roi}
                >
                  Clear ROI
                </button>
              </div>
              {roiStatus && (
                <div
                  className={`status-note ${
                    roiStatus.tone === 'success' ? 'status-note--success' : 'status-note--error'
                  }`}
                >
                  {roiStatus.message}
                </div>
              )}
            </div>
          )}
        </article>
      </section>

      <section className="surface-card family-members-card space-y-3">
        <div className="space-y-1">
          <h2 className="section-title">Family members</h2>
          <p className="dashboard-link-note">
            Proband-first overview with pedigree relations and phenotype labels.
          </p>
        </div>
        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table">
            <thead>
              <tr>
                <th>Sample</th>
                <th>Role</th>
                <th>Father</th>
                <th>Mother</th>
                <th>Phenotype</th>
              </tr>
            </thead>
            <tbody>
              {orderedMembers.map((member) => {
                const ped = pedRows.find((row) => row.iid === member.sample_id);
                const sexSymbol =
                  member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚧';
                return (
                  <tr key={member.sample_id}>
                    <td>
                      <span className="flex items-center gap-1">
                        <span>{sexSymbol}</span>
                        {member.sample_id}
                        {member.affected && (
                          <span className="ml-1" title="Affected">
                            *
                          </span>
                        )}
                      </span>
                    </td>
                    <td>{member.role}</td>
                    <td>{ped?.pid ?? '-'}</td>
                    <td>{ped?.mid ?? '-'}</td>
                    <td>{phenotypeLabel(ped?.phen, member.affected)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default FamilyDetailPage;
