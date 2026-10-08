import React, { useEffect, useMemo, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import PageState from '../../components/PageState';
import { getErrorMessage } from '../../lib/errorMessage';
import ProjectCatalogWorkspace from '../../components/ProjectCatalogWorkspace';
import { withEntityId } from '../../lib/entity';
import DataInventoryDetail from './DataInventoryDetail';
import DeleteFamilyDialog from './DeleteFamilyDialog';
import {
  EMPTY_PROJECTS,
  normalizeProjectIds,
  type FamilyData,
  type ProjectOption,
  type StatusTone,
} from './dataManagementTypes';
import { apiPath } from '../../lib/apiPath';

const DataManagementPage: React.FC = () => {
  const queryClient = useQueryClient();

  const [catalogSearch, setCatalogSearch] = useState('');
  const [selectedFamilyId, setSelectedFamilyId] = useState<string | null>(null);
  const [familyProjectDrafts, setFamilyProjectDrafts] = useState<Record<string, string[]>>({});
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [deleteDialogOpen, setDeleteDialogOpen] = useState(false);
  const [status, setStatus] = useState<{ tone: StatusTone; message: string } | null>(null);

  const {
    data: selectedFamily,
    isLoading: selectedFamilyLoading,
    error: selectedFamilyError,
  } = useQuery<FamilyData>({
    queryKey: ['admin', 'data-inventory', 'family', selectedFamilyId],
    queryFn: async () => {
      const response = await api.get(apiPath`/admin/data/families/${selectedFamilyId}`);
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
      return (response.data as ProjectOption[]).map((entry) => withEntityId(entry));
    },
    retry: false,
  });

  const projects = projectsData ?? EMPTY_PROJECTS;

  useEffect(() => {
    if (!selectedFamily) return;
    setFamilyProjectDrafts((current) => ({
      ...current,
      [selectedFamily.family_id]: [...selectedFamily.projects],
    }));
  }, [selectedFamily]);

  const refreshInventory = async (affectedFamilyId?: string | null) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['admin', 'data-inventory'] }),
      queryClient.invalidateQueries({ queryKey: ['projects'] }),
      queryClient.invalidateQueries({ queryKey: ['families'] }),
      affectedFamilyId
        ? queryClient.invalidateQueries({ queryKey: ['family', affectedFamilyId] })
        : Promise.resolve(),
    ]);
  };

  const runAction = async (
    key: string,
    confirmation: string,
    action: () => Promise<unknown>,
    successMessage: string,
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

  const deleteSelectedFamily = async () => {
    if (!selectedFamilyId) return;
    setBusyKey(`family:${selectedFamilyId}`);
    setStatus(null);
    try {
      await api.delete(apiPath`/admin/families/${selectedFamilyId}`, { params: { confirm: true } });
      const deletedId = selectedFamilyId;
      setDeleteDialogOpen(false);
      setSelectedFamilyId(null);
      await refreshInventory(deletedId);
      setStatus({
        tone: 'success',
        message: `Deleted family ${deletedId} and all linked data.`,
      });
    } catch (error) {
      setStatus({
        tone: 'error',
        message: getErrorMessage(error, 'The family could not be deleted.'),
      });
    } finally {
      setBusyKey(null);
    }
  };

  const saveFamilyProjects = async (familyId: string) => {
    setBusyKey(`family-projects:${familyId}`);
    setStatus(null);

    try {
      await api.put(apiPath`/admin/families/${familyId}/projects`, {
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
        message: getErrorMessage(error, 'Could not update family project assignments.'),
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
      selectedFamily?.family_id === familyId ? selectedFamily.projects : [];
    setFamilyProjectDrafts((current) => ({
      ...current,
      [familyId]: normalizeProjectIds(sourceProjects),
    }));
  };

  const selectedFamilyErrorMessage = useMemo(
    () =>
      selectedFamilyError
        ? getErrorMessage(selectedFamilyError, 'The selected family detail could not be loaded.')
        : null,
    [selectedFamilyError],
  );

  if (projectsLoading) {
    return (
      <PageState
        kicker="Administration"
        title="Loading data management"
        message="Preparing the family catalog and project inventory."
      />
    );
  }

  if (projectsError) {
    return (
      <PageState
        kicker="Administration"
        title="Could not load data management"
        message={getErrorMessage(projectsError, 'The administrative inventory could not be loaded.')}
      />
    );
  }

  return (
    <div className="page-shell admin-compact space-y-5">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Administration</p>
            <h1 className="catalog-card-title">Family and sample data management</h1>
            <p className="catalog-card-copy">
              Select a family to manage its members, samples, project access, assay data, and the
              raw source files used to import it.
            </p>
          </div>
        </div>
      </section>

      {status && (
        <div
          className={`status-note ${
            status.tone === 'error' ? 'status-note--error' : 'status-note--success'
          }`}
        >
          {status.message}
        </div>
      )}

      {/* Family selection — same grouped, collapsible UX as the dashboard. */}
      <section className="surface-card admin-family-select-card space-y-4">
        <div className="space-y-2">
          <p className="page-kicker">Families</p>
          <h2 className="section-title">Select a family</h2>
          <p className="catalog-card-copy">
            Families are grouped by project. Expand a project and choose a family to load its full
            details below.
          </p>
        </div>
        <ProjectCatalogWorkspace
          searchTerm={catalogSearch}
          onSearchTermChange={setCatalogSearch}
          onSelectFamily={setSelectedFamilyId}
          selectedFamilyId={selectedFamilyId}
        />
      </section>

      {selectedFamilyId ? (
        <DataInventoryDetail
          selectedFamilyId={selectedFamilyId}
          selectedFamily={selectedFamily}
          selectedFamilyLoading={selectedFamilyLoading}
          selectedFamilyErrorMessage={selectedFamilyErrorMessage}
          projects={projects}
          familyProjectDrafts={familyProjectDrafts}
          busyKey={busyKey}
          onRunAction={runAction}
          onResetFamilyProjects={resetFamilyProjects}
          onSaveFamilyProjects={saveFamilyProjects}
          onToggleFamilyProject={toggleFamilyProject}
          onRequestDeleteFamily={() => setDeleteDialogOpen(true)}
        />
      ) : (
        <section className="surface-card admin-data-detail">
          <div className="page-state">
            <div className="space-y-2">
              <p className="page-kicker">Administration</p>
              <h2 className="page-state-title">No family selected</h2>
              <p className="page-state-copy">
                Choose a family from the catalog above to inspect members, samples, tracks, and raw
                import files, or to delete the entire family.
              </p>
            </div>
          </div>
        </section>
      )}

      {deleteDialogOpen && selectedFamilyId && (
        <DeleteFamilyDialog
          familyId={selectedFamilyId}
          busy={busyKey === `family:${selectedFamilyId}`}
          onCancel={() => setDeleteDialogOpen(false)}
          onConfirm={deleteSelectedFamily}
        />
      )}
    </div>
  );
};

export default DataManagementPage;
