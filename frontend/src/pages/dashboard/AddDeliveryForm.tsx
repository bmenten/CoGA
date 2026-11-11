import React, { useState } from 'react';
import { useMutation, useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import type {
  FamilyPackageCandidateOut,
  ImportDeliveryOut,
} from '../../lib/apiSchema.generated';
import { getErrorMessage } from '../../lib/errorMessage';
import { useProjectCatalog } from '../../lib/reference';

/** A folder whose families all imported completely, and whose latest import was not undone: the
 * list leaves it out unless imported folders are shown. */
export const isImportedFolder = (pkg: FamilyPackageCandidateOut) => {
  const families = pkg.imported_families ?? [];
  return (
    families.length > 0 &&
    families.every((family) => !family.import_incomplete) &&
    !pkg.changed_during_import
  );
};

const ANALYSIS_TYPES: [string, string][] = [
  ['wgs', 'WGS'],
  ['wgs_trio', 'WGS trio'],
  ['pgt_m', 'PGT-M'],
  ['monogenic_nipt', 'Monogenic NIPT'],
  ['long_read_wgs', 'Long-read WGS'],
  ['long_read_couple', 'Long-read couple'],
];

/**
 * Adds a package folder to Deliveries by hand (docs/data-import.md, "Adding a folder by hand"):
 * CoGA makes its package from the folder, the members and datasets as Discover proposes them,
 * and checks it as a pipeline's delivery. The person then corrects it with Correct… and approves
 * it as any delivery. CoGA writes nothing in the folder.
 */
const AddDeliveryForm: React.FC<{
  onAdded: (delivery: ImportDeliveryOut) => void;
}> = ({ onAdded }) => {
  const [folderPath, setFolderPath] = useState('');
  const [projectId, setProjectId] = useState('');
  const [familyId, setFamilyId] = useState('');
  const [analysisType, setAnalysisType] = useState('');
  const [showImported, setShowImported] = useState(false);
  const { data: projects = [], isLoading: projectsLoading } =
    useProjectCatalog();
  const packagesQuery = useQuery<FamilyPackageCandidateOut[]>({
    queryKey: ['family-import-packages'],
    queryFn: async () =>
      ((await api.get('/family-imports/packages')).data as
        FamilyPackageCandidateOut[] | null) || [],
  });
  const packages = packagesQuery.data ?? [];
  const importedCount = packages.filter(isImportedFolder).length;
  const listed = showImported
    ? packages
    : packages.filter((pkg) => !isImportedFolder(pkg));

  const addMutation = useMutation({
    mutationFn: async () =>
      (
        await api.post('/family-imports/deliveries', {
          folder_path: folderPath.trim(),
          project_id: projectId,
          family_id: familyId.trim() || null,
          analysis_type: analysisType || null,
        })
      ).data as ImportDeliveryOut,
    onSuccess: (delivery) => {
      setFolderPath('');
      setFamilyId('');
      setAnalysisType('');
      onAdded(delivery);
    },
  });

  // A folder chosen from the list: the project of the project folder it lies in, when CoGA has
  // one of that name and none is chosen yet.
  const choose = (path: string) => {
    setFolderPath(path);
    const chosen = packages.find((pkg) => pkg.folder_path === path);
    const project = projects.find(
      (item) => item.name === chosen?.project_folder
    );
    if (project && !projectId) {
      setProjectId(project.id);
    }
  };
  const canAdd =
    !addMutation.isPending &&
    folderPath.trim().length > 0 &&
    projectId.length > 0 &&
    analysisType.length > 0;

  return (
    <details className="text-sm">
      <summary>Add a folder by hand</summary>
      <div className="space-y-3">
        <p className="table-subtle">
          CoGA makes the folder&apos;s package, with the members and datasets
          Discover proposes from it, and checks it as a pipeline&apos;s
          delivery. Check and correct it with Correct…, then approve it. CoGA
          writes nothing in the folder.
        </p>
        <div className="delivery-correction-grid">
          <div className="space-y-1">
            <label className="field-label">
              Folder to add
              <select
                value={
                  listed.some((pkg) => pkg.folder_path === folderPath)
                    ? folderPath
                    : ''
                }
                onChange={(event) => choose(event.target.value)}
                disabled={packagesQuery.isLoading}
              >
                <option value="">
                  {packagesQuery.isLoading
                    ? 'Scanning the import folder…'
                    : 'Select a folder…'}
                </option>
                {listed.map((pkg) => (
                  <option key={pkg.folder_path} value={pkg.folder_path}>
                    {pkg.project_folder ? `${pkg.project_folder} / ` : ''}
                    {pkg.name}
                    {isImportedFolder(pkg)
                      ? ' · imported'
                      : pkg.changed_during_import
                        ? ' · import undone'
                        : (pkg.imported_families ?? []).length
                          ? ' · import incomplete'
                          : ''}
                  </option>
                ))}
              </select>
            </label>
            {importedCount > 0 ? (
              <label className="inline-flex items-center gap-2 text-xs text-(--color-text-muted)">
                <input
                  type="checkbox"
                  checked={showImported}
                  onChange={(event) => setShowImported(event.target.checked)}
                />
                Show imported folders ({importedCount})
              </label>
            ) : null}
          </div>
          <label className="field-label">
            Or the path of the folder to add
            <input
              value={folderPath}
              placeholder="/data/families/FAM001"
              onChange={(event) => setFolderPath(event.target.value)}
            />
          </label>
          <label className="field-label">
            Project to add it to
            <select
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              disabled={projectsLoading}
            >
              <option value="">Select a project…</option>
              {projects.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            Analysis of the folder
            <select
              value={analysisType}
              onChange={(event) => setAnalysisType(event.target.value)}
            >
              <option value="">Select the analysis…</option>
              {ANALYSIS_TYPES.map(([value, label]) => (
                <option key={value} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label className="field-label">
            Family ID of the folder
            <input
              value={familyId}
              placeholder="The folder's name"
              onChange={(event) => setFamilyId(event.target.value)}
            />
          </label>
        </div>
        {addMutation.isError ? (
          <p className="status-note status-note--error">
            {getErrorMessage(
              addMutation.error,
              'The folder could not be added.'
            )}
          </p>
        ) : null}
        <button
          type="button"
          className="form-button"
          disabled={!canAdd}
          onClick={() => addMutation.mutate()}
        >
          {addMutation.isPending ? 'Adding…' : 'Add to Deliveries'}
        </button>
      </div>
    </details>
  );
};

export default AddDeliveryForm;
