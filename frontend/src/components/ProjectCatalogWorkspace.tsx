import React, { useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Link } from 'react-router';
import api from '../lib/api';
import type { ApiFamilySummary } from '../lib/apiTypes';
import { useProjectCatalog } from '../lib/reference';
import { formatUserRef } from '../lib/users';
import FamilyStatusBadge from './FamilyStatusBadge';
import PageState from './PageState';
import VizTooltip from './visualizations/VizTooltip';

/** Date a family was added to the system, e.g. "Jan 15, 2024" (matches ProjectsPage). */
const formatAddedDate = (value?: string): string => {
  if (!value) return '—';
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return '—';
  return new Intl.DateTimeFormat(undefined, {
    year: 'numeric',
    month: 'short',
    day: 'numeric',
  }).format(date);
};

interface ProjectCatalogWorkspaceProps {
  embedded?: boolean;
  searchTerm?: string;
  onSearchTermChange?: (value: string) => void;
  /**
   * When provided, family names become selection buttons (calling this callback)
   * instead of navigation links. Used by the admin Families page to drive an
   * in-page family workspace while keeping the dashboard grouping/UX.
   */
  onSelectFamily?: (familyId: string) => void;
  /** Highlights the currently selected family when in selection mode. */
  selectedFamilyId?: string | null;
}

const ProjectCatalogWorkspace: React.FC<ProjectCatalogWorkspaceProps> = ({
  embedded = false,
  searchTerm,
  onSearchTermChange,
  onSelectFamily,
  selectedFamilyId,
}) => {
  const {
    data = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useProjectCatalog();

  const {
    data: allFamilies = [],
    isLoading: familiesLoading,
    error: familiesError,
  } = useQuery<ApiFamilySummary[]>({
    queryKey: ['families'],
    queryFn: async () => {
      const res = await api.get('/families');
      return res.data as ApiFamilySummary[];
    },
    retry: false,
  });

  const [localSearch, setLocalSearch] = useState('');
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const [sampleTooltip, setSampleTooltip] = useState<{ ids: string[]; x: number; y: number } | null>(
    null,
  );
  const search = searchTerm ?? localSearch;
  const setSearch = onSearchTermChange ?? setLocalSearch;
  const normalizedSearch = search.trim().toLowerCase();

  const toggleProject = (id: string) => {
    setExpanded((prev) => ({ ...prev, [id]: !prev[id] }));
  };

  const renderFamilyName = (familyId: string) => {
    if (onSelectFamily) {
      const active = familyId === selectedFamilyId;
      return (
        <button
          type="button"
          className={`table-link family-select-link${
            active ? ' family-select-link--active' : ''
          }`}
          onClick={() => onSelectFamily(familyId)}
          aria-pressed={active}
        >
          {familyId}
        </button>
      );
    }
    return (
      <Link to={`/families/${familyId}`} className="table-link">
        {familyId}
      </Link>
    );
  };

  // Shared column scaffold for both the per-project nested table and the
  // unassigned-families table, so they stay in lockstep.
  const renderFamilyTableHead = () => (
    <>
      <colgroup>
        <col className="family-catalog-family-column" />
        <col className="family-catalog-samples-column" />
        <col className="family-catalog-status-column" />
        <col className="family-catalog-assigned-column" />
        <col className="family-catalog-reviewed-column" />
        <col className="family-catalog-added-column" />
      </colgroup>
      <thead>
        <tr>
          <th>Family ID</th>
          <th>Samples</th>
          <th>Status</th>
          <th>Assigned to</th>
          <th>Reviewed by</th>
          <th>Date added</th>
        </tr>
      </thead>
    </>
  );

  const renderFamilyRow = (family: ApiFamilySummary) => {
    const sampleIds = family.members.map((member) => member.sample_id);
    return (
      <tr key={family.family_id}>
        <td className="family-catalog-family-cell">{renderFamilyName(family.family_id)}</td>
        <td className="family-catalog-samples-cell">
          <span
            className="family-catalog-sample-count"
            onMouseEnter={(event) =>
              sampleIds.length > 0 &&
              setSampleTooltip({ ids: sampleIds, x: event.clientX, y: event.clientY })
            }
            onMouseMove={(event) =>
              sampleIds.length > 0 &&
              setSampleTooltip({ ids: sampleIds, x: event.clientX, y: event.clientY })
            }
            onMouseLeave={() => setSampleTooltip(null)}
          >
            {family.members.length}
          </span>
        </td>
        <td className="family-catalog-status-cell">
          <FamilyStatusBadge status={family.status} />
        </td>
        <td className="family-catalog-assigned-cell">{formatUserRef(family.assigned_to)}</td>
        <td className="family-catalog-reviewed-cell">{formatUserRef(family.reviewed_by)}</td>
        <td className="family-catalog-added-cell" title="Date added to the system">
          {formatAddedDate(family.created_at)}
        </td>
      </tr>
    );
  };

  const familyMatchesSearch = useCallback(
    (family: ApiFamilySummary) =>
      family.family_id.toLowerCase().includes(normalizedSearch) ||
      family.members.some((member) => member.sample_id.toLowerCase().includes(normalizedSearch)),
    [normalizedSearch],
  );

  const filteredProjects = useMemo(() => {
    return data
      .map((project) => {
        const projectNameMatch =
          normalizedSearch.length === 0 || project.name.toLowerCase().includes(normalizedSearch);
        const matchingProjectSamples =
          normalizedSearch.length === 0
            ? project.samples
            : project.samples.filter((sampleId) => sampleId.toLowerCase().includes(normalizedSearch));
        const matchingFamilies =
          normalizedSearch.length === 0 ? project.families : project.families.filter(familyMatchesSearch);
        const projectDirectMatch = projectNameMatch || matchingProjectSamples.length > 0;
        const visibleFamilies = projectNameMatch
          ? project.families
          : matchingFamilies;
        const visibleSampleIds = new Set<string>([
          ...(projectNameMatch ? project.samples : matchingProjectSamples),
          ...visibleFamilies.flatMap((family) => family.members.map((member) => member.sample_id)),
        ]);

        return {
          ...project,
          projectNameMatch,
          projectDirectMatch,
          matchingFamilies,
          visibleSampleIds: Array.from(visibleSampleIds),
          visibleFamilies,
          visibleFamilyCount: visibleFamilies.length,
          visibleSampleCount: visibleSampleIds.size,
        };
      })
      .filter(
        (project) =>
          normalizedSearch.length === 0 ||
          project.projectDirectMatch ||
          project.visibleFamilies.length > 0,
      );
  }, [data, familyMatchesSearch, normalizedSearch]);

  const { unassignedFamilies, totalFamilies, totalSamples } = useMemo(() => {
    const assignedFamilyIds = new Set<string>();
    data.forEach((project) => {
      project.families.forEach((family) => {
        assignedFamilyIds.add(family.family_id);
      });
    });

    const unassignedFamilies = allFamilies.filter(
      (family) => !assignedFamilyIds.has(family.family_id) && familyMatchesSearch(family),
    );

    const totalFamilies =
      filteredProjects.reduce((sum, project) => sum + project.visibleFamilyCount, 0) +
      unassignedFamilies.length;

    const totalSamples = new Set<string>([
      ...filteredProjects.flatMap((project) => project.visibleSampleIds),
      ...unassignedFamilies.flatMap((family) => family.members.map((member) => member.sample_id)),
    ]).size;

    return { unassignedFamilies, totalFamilies, totalSamples };
  }, [allFamilies, data, familyMatchesSearch, filteredProjects]);

  if (projectsLoading || familiesLoading) {
    if (embedded) {
      return (
        <div className="dashboard-catalog-inline-state">
          <p className="page-kicker">Catalog</p>
          <h2 className="section-title">Loading projects and families</h2>
          <p className="section-copy">
            Preparing the project overview and family catalog for the dashboard.
          </p>
        </div>
      );
    }

    return (
      <PageState
        kicker="Collections"
        title="Loading projects and families"
        message="Preparing the family catalog and project structure."
      />
    );
  }

  if (projectsError || familiesError) {
    const detail =
      (projectsError as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      (familiesError as { response?: { data?: { detail?: string } } })?.response?.data?.detail ||
      (projectsError as Error | undefined)?.message ||
      (familiesError as Error | undefined)?.message ||
      'The family catalog could not be loaded.';

    if (embedded) {
      return (
        <div className="dashboard-catalog-inline-state">
          <p className="page-kicker">Catalog</p>
          <h2 className="section-title">Could not load project catalog</h2>
          <p className="section-copy">{detail}</p>
        </div>
      );
    }

    return <PageState kicker="Collections" title="Could not load families" message={detail} />;
  }

  return (
    <>
      {embedded ? (
        <section className="family-catalog-toolbar family-catalog-toolbar--embedded">
          <div className="family-catalog-summary">
            <span className="badge-chip">Projects {filteredProjects.length}</span>
            <span className="badge-chip">Families {totalFamilies}</span>
            <span className="badge-chip">Samples {totalSamples}</span>
          </div>
        </section>
      ) : (
        <section className="surface-card-flat family-catalog-toolbar">
          <label className="field-label family-catalog-search">
            Search projects, families, or samples
            <input
              type="text"
              placeholder="Search projects, families, or samples..."
              value={search}
              onChange={(event) => setSearch(event.target.value)}
            />
          </label>
          <div className="family-catalog-summary">
            <span className="badge-chip">Projects {filteredProjects.length}</span>
            <span className="badge-chip">Families {totalFamilies}</span>
            <span className="badge-chip">Samples {totalSamples}</span>
          </div>
        </section>
      )}

        <div className="data-table-shell overflow-x-auto">
          <table className="analysis-table family-catalog-table">
            <thead>
              <tr>
                <th>Project catalog</th>
                <th>Reference</th>
              </tr>
            </thead>
            <tbody>
              {filteredProjects.length === 0 && (
                <tr>
                  <td colSpan={2}>
                    <p className="table-empty">No projects match the current search.</p>
                  </td>
                </tr>
              )}
              {filteredProjects.map((project) => {
                const referenceLabel =
                  project.species_name && project.assembly_name
                    ? `${project.species_name} • ${project.assembly_name}${project.assembly_version ? ` ${project.assembly_version}` : ''}`
                    : 'Reference not set';
                const showMatchingFamilies =
                  normalizedSearch.length > 0 && project.matchingFamilies.length > 0;
                const projectExpanded = expanded[project.id] || showMatchingFamilies;

                return (
                  <React.Fragment key={project.id}>
                    <tr>
                      <td className="family-catalog-project-cell">
                        <button
                          onClick={() => toggleProject(project.id)}
                          className="project-toggle project-toggle--compact"
                          aria-expanded={projectExpanded ? 'true' : 'false'}
                        >
                          <span>
                            <span className="project-toggle-title">{project.name}</span>
                            <span className="project-toggle-meta">
                              {project.visibleFamilyCount}{' '}
                              {project.visibleFamilyCount === 1 ? 'family' : 'families'},{' '}
                              {project.visibleSampleCount} {project.visibleSampleCount === 1 ? 'sample' : 'samples'}
                            </span>
                          </span>
                        </button>
                      </td>
                      <td className="family-catalog-reference-cell">{referenceLabel}</td>
                    </tr>
                    {projectExpanded && (
                      <tr className="family-catalog-detail-row">
                        <td colSpan={2}>
                          {project.visibleFamilies.length === 0 ? (
                            <p className="table-empty">No families</p>
                          ) : (
                            <div className="family-catalog-detail overflow-x-auto">
                              <table className="analysis-table family-catalog-nested">
                                {renderFamilyTableHead()}
                                <tbody>
                                  {project.visibleFamilies.map((family) => renderFamilyRow(family))}
                                </tbody>
                              </table>
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </React.Fragment>
                );
              })}
            </tbody>
          </table>
        </div>

      {unassignedFamilies.length > 0 && (
        <section data-testid="unassigned-families" className="surface-card-flat space-y-4">
          <div>
            <h2 className="section-title">Unassigned families</h2>
            <p className="section-copy">
              Families that exist in the catalog but are not linked to a project yet.
            </p>
          </div>
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table family-catalog-nested">
              {renderFamilyTableHead()}
              <tbody>
                {unassignedFamilies.map((family) => renderFamilyRow(family))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {sampleTooltip && (
        <VizTooltip x={sampleTooltip.x} y={sampleTooltip.y}>
          {sampleTooltip.ids.join(', ')}
        </VizTooltip>
      )}
    </>
  );
};

export default ProjectCatalogWorkspace;
