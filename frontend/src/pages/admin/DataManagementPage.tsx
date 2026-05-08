import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { Link } from 'react-router-dom';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { withEntityId } from '../../lib/entity';
import DataInventoryDetail from './DataInventoryDetail';
import DataInventorySidebar from './DataInventorySidebar';
import {
  DEFAULT_PAGE_SIZE,
  EMPTY_PROJECTS,
  EMPTY_SUMMARY_ITEMS,
  formatCount,
  type FamilyData,
  type FamilyInventoryPage,
  type ProjectOption,
} from './dataManagementTypes';

type StatusTone = 'success' | 'error';

const normalizeProjectIds = (projectIds: string[]) =>
  Array.from(new Set(projectIds)).sort((left, right) =>
    left.localeCompare(right)
  );

const getErrorMessage = (error: unknown, fallback: string): string => {
  if (typeof error !== 'object' || error === null) {
    return fallback;
  }

  const responseDetail = (
    error as { response?: { data?: { detail?: string } } }
  )?.response?.data?.detail;
  if (responseDetail) {
    return responseDetail;
  }

  return (error as { message?: string }).message || fallback;
};

const DataManagementPage: React.FC = () => {
  const queryClient = useQueryClient();

  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  const [familyProjectDrafts, setFamilyProjectDrafts] = useState<
    Record<string, string[]>
  >({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [status, setStatus] = useState<{
    tone: StatusTone;
    message: string;
  } | null>(null);

  const {
    data: inventoryPage,
    isLoading: inventoryLoading,
    isFetching: inventoryFetching,
    error: inventoryError,
  } = useQuery<FamilyInventoryPage>({
    queryKey: ['admin', 'data-inventory', page, search],
    queryFn: async () => {
      const response = await api.get('/admin/data', {
        params: {
          page,
          page_size: DEFAULT_PAGE_SIZE,
          search: search.trim() || undefined,
        },
      });
      return response.data as FamilyInventoryPage;
    },
    retry: false,
  });

  const {
    data: selectedFamily,
    isLoading: selectedFamilyLoading,
    error: selectedFamilyError,
  } = useQuery<FamilyData>({
    queryKey: ['admin', 'data-inventory', 'family', selectedFamilyId],
    queryFn: async () => {
      const response = await api.get(
        `/admin/data/families/${selectedFamilyId}`
      );
      return response.data as FamilyData;
    },
    enabled: Boolean(selectedFamilyId),
    retry: false,
  });

  const {
    data: projectsData,
    isLoading: projectsLoading,
    error: projectsError,
  } = useQuery<ProjectOption[]>({
    queryKey: ['projects'],
    queryFn: async () => {
      const response = await api.get('/projects');
      return (response.data as any[]).map((entry) =>
        withEntityId(entry)
      ) as ProjectOption[];
    },
    retry: false,
  });

  const summaries = inventoryPage?.items ?? EMPTY_SUMMARY_ITEMS;
  const projects = projectsData ?? EMPTY_PROJECTS;

  useEffect(() => {
    if (!selectedFamily) return;

    setFamilyProjectDrafts((current) => ({
      ...current,
      [selectedFamily.family_id]: [...selectedFamily.projects],
    }));
  }, [selectedFamily]);

  useEffect(() => {
    if (summaries.length === 0) {
      setSelectedFamilyId(null);
      return;
    }

    if (
      !selectedFamilyId ||
      !summaries.some((family) => family.family_id === selectedFamilyId)
    ) {
      setSelectedFamilyId(summaries[0].family_id);
    }
  }, [selectedFamilyId, summaries]);

  useEffect(() => {
    const total = inventoryPage?.total ?? 0;
    const lastPage = Math.max(Math.ceil(total / DEFAULT_PAGE_SIZE), 1);
    if (page > lastPage) {
      setPage(lastPage);
    }
  }, [inventoryPage?.total, page]);

  const selectedFamilySummary = useMemo(
    () =>
      summaries.find((family) => family.family_id === selectedFamilyId) ?? null,
    [selectedFamilyId, summaries]
  );

  const totals = useMemo(() => {
    const familyCount = inventoryPage?.total ?? 0;
    const visibleCount = summaries.length;
    const selectedSampleCount = selectedFamily?.sample_count ?? 0;
    const selectedTrackRecordCount = selectedFamily?.total_records ?? 0;
    return {
      familyCount,
      visibleCount,
      selectedSampleCount,
      selectedTrackRecordCount,
    };
  }, [inventoryPage?.total, selectedFamily, summaries.length]);

  const refreshInventory = async (affectedFamilyId?: string | null) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['admin', 'data-inventory'] }),
      selectedFamilyId
        ? queryClient.invalidateQueries({
            queryKey: ['admin', 'data-inventory', 'family', selectedFamilyId],
          })
        : Promise.resolve(),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: ['families'] }),
      affectedFamilyId
        ? queryClient.invalidateQueries({
            queryKey: ['family', affectedFamilyId],
          })
        : Promise.resolve(),
    ]);
  };

  const runAction = async (
    key: string,
    confirmation: string,
    action: () => Promise<unknown>,
    successMessage: string
  ) => {
    if (!window.confirm(confirmation)) return;

    setBusyKey(key);
    setStatus(null);

    try {
      await action();
      await refreshInventory(selectedFamilyId);
      setStatus({ tone: 'success', message: successMessage });
    } catch (error) {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'The requested data operation failed.'),
      });
    } finally {
      setBusyKey(null);
    }
  };

  const saveFamilyProjects = async (familyId: string) => {
    setBusyKey(`family-projects:${familyId}`);
    setStatus(null);

    try {
      await api.put(`/admin/families/${familyId}/projects`, {
        project_ids: normalizeProjectIds(familyProjectDrafts[familyId] ?? []),
      });
      await refreshInventory(familyId);
      setStatus({
        tone: 'success',
        message: `Saved project access for family ${familyId}.`,
      });
    } catch (error) {
      setStatus({
        tone: 'error',
        message: getErrorMessage(
          error,
          'Could not update family project assignments.'
        ),
      });
    } finally {
      setBusyKey(null);
    }
  };

  const toggleFamilyProject = (familyId: string, projectId: string) => {
    setFamilyProjectDrafts((current) => {
      const next = new Set(current[familyId] ?? []);
      if (next.has(projectId)) {
        next.delete(projectId);
      } else {
        next.add(projectId);
      }
      return { ...current, [familyId]: normalizeProjectIds(Array.from(next)) };
    });
  };

  const resetFamilyProjects = (familyId: string) => {
    const sourceProjects =
      selectedFamily?.family_id === familyId
        ? selectedFamily.projects
        : (summaries.find((family) => family.family_id === familyId)
            ?.projects ?? []);

    setFamilyProjectDrafts((current) => ({
      ...current,
      [familyId]: normalizeProjectIds(sourceProjects),
    }));
  };

  if (inventoryLoading || projectsLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading data management"
        message="Preparing family, sample, and track inventory."
      />
    );
  }

  if (inventoryError || projectsError) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load data management"
        message={getErrorMessage(
          inventoryError ?? projectsError,
          'The administrative inventory could not be loaded.'
        )}
      />
    );
  }

  return (
    <div className="page-shell space-y-8">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">
              Family and sample data management
            </h1>
            <p className="catalog-card-copy">
              Remove tracks from a sample, remove a sample from a family, or
              delete an entire family and all linked assay data.
            </p>
          </div>
        </div>
      </section>

      <section className="surface-card-flat space-y-4">
        <div className="space-y-2">
          <h2 className="section-title">Admin management areas</h2>
          <p className="section-copy">
            Keep each operational domain separate to reduce noise and make bulk
            administration workflows easier.
          </p>
        </div>
        <div className="compact-toolbar dashboard-toolbar">
          <Link to="/projects" className="button-secondary hover:no-underline">
            Projects & access
          </Link>
          <Link
            to="/admin/users"
            className="button-secondary hover:no-underline"
          >
            Users
          </Link>
          <Link
            to="/admin/data/clickhouse"
            className="button-secondary hover:no-underline"
          >
            ClickHouse tables
          </Link>
          <Link
            to="/admin/data/presets"
            className="button-secondary hover:no-underline"
          >
            Preset filters
          </Link>
          <Link
            to="/admin/data/tags"
            className="button-secondary hover:no-underline"
          >
            Variant tags
          </Link>
          <Link
            to="/admin/data/logs"
            className="button-secondary hover:no-underline"
          >
            Audit logs
          </Link>
          <Link to="/reference-data" className="button-grey hover:no-underline">
            Organisms & assemblies
          </Link>
          <Link to="/panels" className="button-grey hover:no-underline">
            Gene panels
          </Link>
        </div>
      </section>

      {status && (
        <div
          className={`status-note ${
            status.tone === 'error'
              ? 'status-note--error'
              : 'status-note--success'
          }`}
        >
          {status.message}
        </div>
      )}

      <section
        className="surface-card-muted admin-data-summary"
        aria-label="Inventory overview"
      >
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Matched families</span>
          <strong className="admin-data-summary-value">
            {formatCount(totals.familyCount)}
          </strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Visible on page</span>
          <strong className="admin-data-summary-value">
            {formatCount(totals.visibleCount)}
          </strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">
            Selected family records
          </span>
          <strong className="admin-data-summary-value">
            {formatCount(totals.selectedTrackRecordCount)}
          </strong>
        </div>
        <div className="admin-data-summary-item">
          <span className="admin-data-summary-label">Selected samples</span>
          <strong className="admin-data-summary-value">
            {formatCount(totals.selectedSampleCount)}
          </strong>
        </div>
      </section>

      <div className="admin-data-layout">
        <DataInventorySidebar
          search={search}
          page={page}
          inventoryPage={inventoryPage}
          inventoryFetching={inventoryFetching}
          selectedFamilyId={selectedFamilyId}
          summaries={summaries}
          onSearchChange={(value) => {
            setSearch(value);
            setPage(1);
          }}
          onPageChange={setPage}
          onSelectFamily={setSelectedFamilyId}
        />
        <DataInventoryDetail
          selectedFamilyId={selectedFamilyId}
          selectedFamilySummary={selectedFamilySummary}
          selectedFamily={selectedFamily}
          selectedFamilyLoading={selectedFamilyLoading}
          selectedFamilyErrorMessage={
            selectedFamilyError
              ? getErrorMessage(
                  selectedFamilyError,
                  'The selected family detail could not be loaded.'
                )
              : null
          }
          projects={projects}
          familyProjectDrafts={familyProjectDrafts}
          busyKey={busyKey}
          onRunAction={runAction}
          onResetFamilyProjects={resetFamilyProjects}
          onSaveFamilyProjects={saveFamilyProjects}
          onToggleFamilyProject={toggleFamilyProject}
        />
      </div>
    </div>
  );
};

export default DataManagementPage;
