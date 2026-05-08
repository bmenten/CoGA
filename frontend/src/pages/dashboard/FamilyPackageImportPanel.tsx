import React, { useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';

import api from '../../lib/api';
import type { ApiFamilySummary } from '../../lib/apiTypes';
import { getErrorMessage } from '../../lib/errorMessage';
import { useProjectCatalog } from '../../lib/reference';

type ImportStatus = 'queued' | 'validating' | 'running' | 'completed' | 'failed';
type PackageTargetMode = 'new' | 'existing';
type ImportConflictMode = 'cancel' | 'update' | 'overwrite';

type ValidationIssue = {
  code: string;
  message: string;
  dataset?: string | null;
  sample_id?: string | null;
  path?: string | null;
};

type DatasetSummary = {
  dataset_type: string;
  enabled: boolean;
  status: string;
  files: string[];
  samples: string[];
  message?: string | null;
  summary: Record<string, unknown>;
};

type FileAvailability = {
  role: string;
  path: string;
  exists: boolean;
  sample_id?: string | null;
};

type ManifestDatasetAvailability = {
  dataset_type: string;
  enabled: boolean;
  complete: boolean;
  files: FileAvailability[];
  samples: string[];
  message?: string | null;
};

type ManifestBuildResult = {
  valid: boolean;
  family_id?: string | null;
  ped_path?: string | null;
  manifest_path: string;
  naming_scheme: string;
  sample_ids: string[];
  manifest_yaml: string;
  datasets: ManifestDatasetAvailability[];
  errors: ValidationIssue[];
  warnings: ValidationIssue[];
  metadata: Record<string, unknown>;
};

type ManifestWriteResult = {
  manifest_path: string;
  validation: {
    valid: boolean;
    errors: ValidationIssue[];
    warnings: ValidationIssue[];
  };
};

type FamilyImportJob = {
  _id: string;
  submitted_path: string;
  family_id?: string | null;
  project_id?: string | null;
  status: ImportStatus;
  dry_run: boolean;
  requested_by: string;
  requested_at: string;
  started_at?: string | null;
  heartbeat_at?: string | null;
  completed_at?: string | null;
  validation_errors: ValidationIssue[];
  validation_warnings: ValidationIssue[];
  logs: string[];
  datasets: DatasetSummary[];
  metadata: Record<string, unknown>;
  error?: string | null;
};

const activeStatuses = new Set<ImportStatus>(['queued', 'validating', 'running']);

const formatTimestamp = (value?: string | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
};

const issueLabel = (issue: ValidationIssue) =>
  [issue.dataset, issue.sample_id, issue.path].filter(Boolean).join(' · ');

const FamilyPackageImportPanel: React.FC = () => {
  const queryClient = useQueryClient();
  const [targetMode, setTargetMode] = useState<PackageTargetMode>('new');
  const [folderPath, setFolderPath] = useState('');
  const [pedPath, setPedPath] = useState('');
  const [hpoInput, setHpoInput] = useState('');
  const [notes, setNotes] = useState('');
  const [namingScheme, setNamingScheme] = useState('standard_v1');
  const [manifestYaml, setManifestYaml] = useState('');
  const [manifestOverwrite, setManifestOverwrite] = useState(false);
  const [manifestResult, setManifestResult] = useState<ManifestBuildResult | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [selectedProjectId, setSelectedProjectId] = useState('');
  const [selectedFamilyId, setSelectedFamilyId] = useState('');
  const [conflictMode, setConflictMode] = useState<ImportConflictMode>('cancel');
  const [jobId, setJobId] = useState<string | null>(null);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [statusTone, setStatusTone] = useState<'success' | 'error'>('success');
  const {
    data: projectOptions = [],
    isLoading: projectsLoading,
    error: projectsError,
  } = useProjectCatalog();

  const familiesQuery = useQuery<ApiFamilySummary[]>({
    queryKey: ['families'],
    queryFn: async () => {
      const response = await api.get('/families');
      return response.data as ApiFamilySummary[];
    },
  });

  React.useEffect(() => {
    if (projectOptions.length === 0) {
      setSelectedProjectId('');
      return;
    }
    setSelectedProjectId((current) =>
      current && projectOptions.some((project) => project.id === current)
        ? current
        : projectOptions[0].id
    );
  }, [projectOptions]);

  React.useEffect(() => {
    const families = familiesQuery.data || [];
    if (targetMode !== 'existing') {
      return;
    }
    if (families.length === 0) {
      setSelectedFamilyId('');
      return;
    }
    setSelectedFamilyId((current) =>
      current && families.some((family) => family.family_id === current)
        ? current
        : families[0].family_id
    );
  }, [familiesQuery.data, targetMode]);

  const jobQuery = useQuery<FamilyImportJob>({
    queryKey: ['family-import-job', jobId],
    queryFn: async () => {
      const response = await api.get(`/family-imports/${jobId}`);
      return response.data as FamilyImportJob;
    },
    enabled: Boolean(jobId),
    refetchInterval: (query) => {
      const payload = query.state.data as FamilyImportJob | undefined;
      return payload && activeStatuses.has(payload.status) ? 2500 : false;
    },
    retry: false,
  });

  const recentJobsQuery = useQuery<FamilyImportJob[]>({
    queryKey: ['family-import-jobs'],
    queryFn: async () => {
      const response = await api.get('/family-imports', { params: { limit: 25 } });
      return response.data as FamilyImportJob[];
    },
    refetchInterval: (query) => {
      const jobs = query.state.data as FamilyImportJob[] | undefined;
      return jobs?.some((job) => activeStatuses.has(job.status)) ? 5000 : 15000;
    },
  });

  const hpoTerms = useMemo(
    () =>
      hpoInput
        .split(/[\s,;]+/)
        .map((term) => term.trim())
        .filter(Boolean),
    [hpoInput],
  );
  const targetFamilyId = targetMode === 'existing' ? selectedFamilyId : '';

  const discoverManifestMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/family-imports/manifest/discover', {
        folder_path: folderPath.trim(),
        ped_path: pedPath.trim() || null,
        family_id: targetFamilyId || null,
        naming_scheme: namingScheme,
        hpo_terms: hpoTerms,
        notes: notes.trim() || null,
      });
      return response.data as ManifestBuildResult;
    },
    onSuccess: (result) => {
      setManifestResult(result);
      setManifestYaml(result.manifest_yaml);
      setStatusTone(result.valid ? 'success' : 'error');
      setStatusMessage(
        result.valid
          ? `Prepared manifest for ${result.family_id || 'package'}.`
          : 'Manifest draft has validation issues.'
      );
    },
    onError: (error) => {
      setStatusTone('error');
      setStatusMessage(getErrorMessage(error, 'Manifest discovery failed.'));
    },
  });

  const writeManifestMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/family-imports/manifest/write', {
        folder_path: folderPath.trim(),
        manifest_yaml: manifestYaml,
        overwrite: manifestOverwrite,
      });
      return response.data as ManifestWriteResult;
    },
    onSuccess: (result) => {
      setStatusTone(result.validation.valid ? 'success' : 'error');
      setStatusMessage(
        result.validation.valid
          ? `Wrote ${result.manifest_path}.`
          : `Wrote ${result.manifest_path}, but validation still has issues.`
      );
    },
    onError: (error) => {
      setStatusTone('error');
      setStatusMessage(getErrorMessage(error, 'Manifest could not be written.'));
    },
  });

  const startImportMutation = useMutation({
    mutationFn: async () => {
      const response = await api.post('/family-imports', {
        folder_path: folderPath.trim(),
        project_id: selectedProjectId || null,
        dry_run: dryRun,
        family_id: targetFamilyId || null,
        conflict_mode: conflictMode,
      });
      return response.data as FamilyImportJob;
    },
    onSuccess: (job) => {
      setJobId(job._id);
      setStatusTone('success');
      setStatusMessage(`Started ${dryRun ? 'dry-run' : 'import'} job ${job._id}.`);
      queryClient.setQueryData(['family-import-job', job._id], job);
      queryClient.invalidateQueries({ queryKey: ['family-import-jobs'] });
    },
    onError: (error) => {
      setStatusTone('error');
      setStatusMessage(getErrorMessage(error, 'Family package import could not be started.'));
    },
  });

  const currentJob = jobQuery.data;
  const recentJobs = recentJobsQuery.data || [];
  const active = currentJob ? activeStatuses.has(currentJob.status) : startImportMutation.isPending;
  const canSubmit =
    folderPath.trim().length > 0 &&
    !startImportMutation.isPending &&
    !active &&
    (dryRun || Boolean(selectedProjectId)) &&
    (targetMode === 'new' || Boolean(targetFamilyId));
  const canDiscover =
    folderPath.trim().length > 0 &&
    !discoverManifestMutation.isPending &&
    (targetMode === 'new' || Boolean(targetFamilyId));
  const canWriteManifest =
    folderPath.trim().length > 0 && manifestYaml.trim().length > 0 && !writeManifestMutation.isPending;

  const datasetCounts = useMemo(() => {
    const imported = currentJob?.datasets.filter((dataset) => dataset.status === 'imported').length ?? 0;
    const registered = currentJob?.datasets.filter((dataset) => dataset.status === 'registered').length ?? 0;
    const failed = currentJob?.datasets.filter((dataset) => dataset.status === 'failed').length ?? 0;
    return { imported, registered, failed };
  }, [currentJob?.datasets]);

  return (
    <section className="surface-card space-y-4 family-package-import-panel">
      <div className="catalog-card-header">
        <div>
          <p className="page-kicker">Package Import</p>
          <h2 className="catalog-card-title">Folder package</h2>
        </div>
        <span className="badge-chip badge-chip--signature">
          {currentJob?.status ?? 'Idle'}
        </span>
      </div>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.4fr)_minmax(260px,0.6fr)]">
        <label className="field-label" htmlFor="family-package-folder">
          Family folder path
          <input
            id="family-package-folder"
            value={folderPath}
            onChange={(event) => setFolderPath(event.target.value)}
            placeholder="/data/families/FAM001"
          />
        </label>
        <label className="field-label" htmlFor="family-package-project">
          Project
          <select
            id="family-package-project"
            value={selectedProjectId}
            disabled={projectsLoading || projectOptions.length === 0}
            onChange={(event) => setSelectedProjectId(event.target.value)}
          >
            {projectOptions.length === 0 ? (
              <option value="">
                {projectsLoading ? 'Loading projects...' : 'No accessible projects'}
              </option>
            ) : (
              projectOptions.map((project) => (
                <option key={project.id} value={project.id}>
                  {project.name}
                </option>
              ))
            )}
          </select>
        </label>
      </div>

      <div className="surface-card-muted space-y-4">
        <div className="catalog-card-header">
          <div>
            <p className="page-kicker">Import Target</p>
            <h3 className="section-title">Family destination</h3>
          </div>
          <span className="badge-chip">
            {targetMode === 'existing' ? targetFamilyId || 'Select family' : 'Create family'}
          </span>
        </div>
        <div className="grid gap-4 xl:grid-cols-[minmax(260px,0.7fr)_minmax(260px,0.9fr)_minmax(260px,0.8fr)]">
          <div className="field-label">
            Target
            <div className="mt-2 flex flex-wrap gap-2">
              <button
                type="button"
                className={`pill-toggle ${targetMode === 'new' ? 'pill-toggle--active' : ''}`}
                aria-pressed={targetMode === 'new'}
                onClick={() => setTargetMode('new')}
              >
                New family
              </button>
              <button
                type="button"
                className={`pill-toggle ${targetMode === 'existing' ? 'pill-toggle--active' : ''}`}
                aria-pressed={targetMode === 'existing'}
                onClick={() => setTargetMode('existing')}
              >
                Existing family
              </button>
            </div>
          </div>
          <label className="field-label" htmlFor="family-package-existing-family">
            Existing family
            <select
              id="family-package-existing-family"
              value={selectedFamilyId}
              disabled={targetMode !== 'existing' || familiesQuery.isLoading || (familiesQuery.data || []).length === 0}
              onChange={(event) => setSelectedFamilyId(event.target.value)}
            >
              {familiesQuery.isLoading ? (
                <option value="">Loading families...</option>
              ) : (familiesQuery.data || []).length === 0 ? (
                <option value="">No accessible families</option>
              ) : (
                (familiesQuery.data || []).map((family) => (
                  <option key={family.family_id} value={family.family_id}>
                    {family.family_id}
                  </option>
                ))
              )}
            </select>
          </label>
          <label className="field-label" htmlFor="family-package-conflict-mode">
            Existing data policy
            <select
              id="family-package-conflict-mode"
              value={conflictMode}
              onChange={(event) => setConflictMode(event.target.value as ImportConflictMode)}
            >
              <option value="cancel">Cancel if family or samples exist</option>
              <option value="update">Update: add missing datasets only</option>
              <option value="overwrite">Overwrite imported dataset rows</option>
            </select>
          </label>
        </div>
        {familiesQuery.error ? (
          <p className="status-note status-note--error">
            {getErrorMessage(familiesQuery.error, 'Family list could not be loaded.')}
          </p>
        ) : null}
      </div>

      <div className="surface-card-muted space-y-4">
        <div className="catalog-card-header">
          <div>
            <p className="page-kicker">Manifest Builder</p>
            <h3 className="section-title">Discover available data</h3>
          </div>
          <span className="badge-chip">{manifestResult?.family_id || 'No draft'}</span>
        </div>

        <div className="grid gap-4 xl:grid-cols-4">
          <label className="field-label" htmlFor="family-package-ped">
            PED path
            <input
              id="family-package-ped"
              value={pedPath}
              onChange={(event) => setPedPath(event.target.value)}
              placeholder="family.ped"
            />
          </label>
          <label className="field-label" htmlFor="family-package-scheme">
            Naming scheme
            <select
              id="family-package-scheme"
              value={namingScheme}
              onChange={(event) => setNamingScheme(event.target.value)}
            >
              <option value="standard_v1">Standard v1</option>
            </select>
          </label>
          <label className="field-label xl:col-span-2" htmlFor="family-package-hpo">
            HPO terms
            <input
              id="family-package-hpo"
              value={hpoInput}
              onChange={(event) => setHpoInput(event.target.value)}
              placeholder="HP:0001250, HP:0004322"
            />
          </label>
        </div>

        <label className="field-label" htmlFor="family-package-notes">
          Notes
          <textarea
            id="family-package-notes"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            className="mono-panel !h-24"
          />
        </label>

        <div className="action-row">
          <button
            type="button"
            className="button-secondary"
            disabled={!canDiscover}
            onClick={() => discoverManifestMutation.mutate()}
          >
            {discoverManifestMutation.isPending ? 'Scanning...' : 'Discover manifest'}
          </button>
          <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
            <input
              type="checkbox"
              checked={manifestOverwrite}
              onChange={(event) => setManifestOverwrite(event.target.checked)}
            />
            Overwrite manifest.yaml
          </label>
          <button
            type="button"
            className="button-secondary"
            disabled={!canWriteManifest}
            onClick={() => writeManifestMutation.mutate()}
          >
            {writeManifestMutation.isPending ? 'Writing...' : 'Write manifest.yaml'}
          </button>
        </div>

        {manifestResult ? (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>Dataset</th>
                  <th>Enabled</th>
                  <th>Samples</th>
                  <th>Available files</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {manifestResult.datasets.map((dataset) => {
                  const availableFiles = dataset.files.filter((file) => file.exists).length;
                  return (
                    <tr key={dataset.dataset_type}>
                      <td>{dataset.dataset_type}</td>
                      <td>{dataset.enabled ? 'Yes' : 'No'}</td>
                      <td>{dataset.samples.length ? dataset.samples.join(', ') : '—'}</td>
                      <td>
                        {availableFiles} / {dataset.files.length}
                      </td>
                      <td>{dataset.message || (dataset.complete ? 'Available' : 'Missing')}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        ) : null}

        {manifestResult?.errors.length ? (
          <ul className="space-y-2 text-sm text-[var(--color-variant-del)]">
            {manifestResult.errors.map((issue, index) => (
              <li key={`${issue.code}-${index}`}>
                {issue.message}
                {issueLabel(issue) ? ` (${issueLabel(issue)})` : ''}
              </li>
            ))}
          </ul>
        ) : null}

        {manifestYaml ? (
          <label className="field-label" htmlFor="family-package-manifest-yaml">
            manifest.yaml preview
            <textarea
              id="family-package-manifest-yaml"
              value={manifestYaml}
              onChange={(event) => setManifestYaml(event.target.value)}
              className="mono-panel !h-96 !text-xs"
            />
          </label>
        ) : null}
      </div>

      <div className="action-row">
        <label className="inline-flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <input
            type="checkbox"
            checked={dryRun}
            onChange={(event) => setDryRun(event.target.checked)}
          />
          Dry run
        </label>
        <button
          type="button"
          className="form-button"
          disabled={!canSubmit}
          onClick={() => startImportMutation.mutate()}
        >
          {startImportMutation.isPending ? 'Starting...' : dryRun ? 'Validate package' : 'Start import'}
        </button>
      </div>

      {projectsError ? (
        <p className="status-note status-note--error">Project list could not be loaded.</p>
      ) : null}
      {!dryRun && !selectedProjectId ? (
        <p className="status-note status-note--error">Choose a project before starting an import.</p>
      ) : null}
      {statusMessage ? (
        <p className={`status-note ${statusTone === 'success' ? 'status-note--success' : 'status-note--error'}`}>
          {statusMessage}
        </p>
      ) : null}
      {jobQuery.error ? (
        <p className="status-note status-note--error">
          {getErrorMessage(jobQuery.error, 'Import job status could not be loaded.')}
        </p>
      ) : null}

      <div className="surface-card-muted space-y-4">
        <div className="catalog-card-header">
          <div>
            <p className="page-kicker">Job History</p>
            <h3 className="section-title">Recent family imports</h3>
          </div>
          <span className="badge-chip">{recentJobs.length}</span>
        </div>
        {recentJobsQuery.error ? (
          <p className="status-note status-note--error">
            {getErrorMessage(recentJobsQuery.error, 'Import job history could not be loaded.')}
          </p>
        ) : null}
        {recentJobs.length > 0 ? (
          <div className="data-table-shell overflow-x-auto">
            <table className="analysis-table table-sticky">
              <thead>
                <tr>
                  <th>Status</th>
                  <th>Family</th>
                  <th>Path</th>
                  <th>Requested</th>
                  <th>Last update</th>
                  <th>Mode</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                {recentJobs.map((job) => (
                  <tr key={job._id}>
                    <td>{job.status}</td>
                    <td>{job.family_id || '—'}</td>
                    <td className="font-mono text-xs">{job.submitted_path}</td>
                    <td>{formatTimestamp(job.requested_at)}</td>
                    <td>{formatTimestamp(job.heartbeat_at || job.completed_at)}</td>
                    <td>{job.dry_run ? 'Dry run' : 'Import'}</td>
                    <td>
                      <button
                        type="button"
                        className="button-secondary"
                        onClick={() => {
                          setJobId(job._id);
                          setFolderPath(job.submitted_path);
                          setDryRun(job.dry_run);
                          setSelectedProjectId(job.project_id || selectedProjectId);
                          const requestedFamilyId =
                            typeof job.metadata?.requested_family_id === 'string'
                              ? job.metadata.requested_family_id
                              : '';
                          const storedConflictMode =
                            typeof job.metadata?.conflict_mode === 'string'
                              ? job.metadata.conflict_mode
                              : 'cancel';
                          setTargetMode(requestedFamilyId ? 'existing' : 'new');
                          setSelectedFamilyId(requestedFamilyId);
                          if (['cancel', 'update', 'overwrite'].includes(storedConflictMode)) {
                            setConflictMode(storedConflictMode as ImportConflictMode);
                          }
                          queryClient.setQueryData(['family-import-job', job._id], job);
                        }}
                      >
                        View
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="table-subtle">No family import jobs have been submitted yet.</p>
        )}
      </div>

      {currentJob ? (
        <div className="surface-card-muted space-y-5">
          <div className="grid gap-3 md:grid-cols-4">
            <div className="stat-card">
              <span className="stat-label">Status</span>
              <span className="stat-value !text-[1.35rem]">{currentJob.status}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Family</span>
              <span className="stat-value !text-[1.35rem]">{currentJob.family_id || '—'}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Imported</span>
              <span className="stat-value !text-[1.35rem]">{datasetCounts.imported}</span>
            </div>
            <div className="stat-card">
              <span className="stat-label">Registered</span>
              <span className="stat-value !text-[1.35rem]">{datasetCounts.registered}</span>
            </div>
          </div>

          <div className="text-sm leading-7 text-[var(--color-text-muted)]">
            Requested {formatTimestamp(currentJob.requested_at)} · Last update{' '}
            {formatTimestamp(currentJob.heartbeat_at)}
          </div>

          {currentJob.error ? (
            <p className="status-note status-note--error">{currentJob.error}</p>
          ) : null}

          {currentJob.validation_errors.length > 0 ? (
            <div>
              <h3 className="eyebrow-label">Validation Errors</h3>
              <ul className="mt-3 space-y-2 text-sm text-[var(--color-variant-del)]">
                {currentJob.validation_errors.map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>
                    {issue.message}
                    {issueLabel(issue) ? ` (${issueLabel(issue)})` : ''}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {currentJob.validation_warnings.length > 0 ? (
            <div>
              <h3 className="eyebrow-label">Warnings</h3>
              <ul className="mt-3 space-y-2 text-sm text-[var(--color-text-muted)]">
                {currentJob.validation_warnings.map((issue, index) => (
                  <li key={`${issue.code}-${index}`}>
                    {issue.message}
                    {issueLabel(issue) ? ` (${issueLabel(issue)})` : ''}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {currentJob.datasets.length > 0 ? (
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table table-sticky">
                <thead>
                  <tr>
                    <th>Dataset</th>
                    <th>Status</th>
                    <th>Samples</th>
                    <th>Files</th>
                    <th>Summary</th>
                  </tr>
                </thead>
                <tbody>
                  {currentJob.datasets.map((dataset) => (
                    <tr key={dataset.dataset_type}>
                      <td>{dataset.dataset_type}</td>
                      <td>{dataset.status}</td>
                      <td>{dataset.samples.length ? dataset.samples.join(', ') : '—'}</td>
                      <td>{dataset.files.length}</td>
                      <td>{dataset.message || (dataset.enabled ? 'Ready' : 'Optional')}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : null}

          {currentJob.logs.length > 0 ? (
            <div>
              <h3 className="eyebrow-label">Log</h3>
              <div className="mono-panel mt-3 max-h-44 overflow-y-auto !text-xs">
                {currentJob.logs.map((line, index) => (
                  <div key={`${line}-${index}`}>{line}</div>
                ))}
              </div>
            </div>
          ) : null}

          {currentJob.status === 'completed' && currentJob.family_id && !currentJob.dry_run ? (
            <Link to={`/families/${currentJob.family_id}`} className="button-secondary hover:no-underline">
              Open imported family
            </Link>
          ) : null}
          {datasetCounts.failed > 0 ? (
            <p className="status-note status-note--error">
              {datasetCounts.failed} dataset import step(s) failed.
            </p>
          ) : null}
        </div>
      ) : null}
    </section>
  );
};

export default FamilyPackageImportPanel;
