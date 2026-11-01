import React from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { routePath } from '../../lib/routePath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { FamilyPgsOut } from '../../lib/apiSchema.generated';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import FamilyCaseHeader from './FamilyCaseHeader';
import { useFamilyReference } from '../../lib/reference';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';

const FamilyPgsPage: React.FC = () => {
  const { familyId = '' } = useParams<{ familyId: string }>();
  const location = useLocation();
  const client = useQueryClient();
  const requestedProject = new URLSearchParams(location.search).get('project_id') || undefined;
  const family = useQuery({
    queryKey: ['family', familyId],
    queryFn: async () => (await api.get<ApiFamilyRecord>(apiPath`/families/${familyId}`)).data,
  });
  const reference = useFamilyReference(family.data?.projects, requestedProject);
  const projectId = reference.projectId ?? requestedProject;
  const params = projectId ? { project_id: projectId } : {};
  const queryKey = ['family-pgs', familyId, projectId];
  const query = useQuery({
    queryKey,
    enabled: Boolean(family.data) && !reference.isLoading && !reference.isError,
    queryFn: async () => (await api.get<FamilyPgsOut>(apiPath`/families/${familyId}/pgs`, { params })).data,
    refetchInterval: (q) => q.state.data?.runs.some((run) => ['queued', 'running'].includes(run.status)) ? 2000 : false,
  });
  const calculate = useMutation({
    mutationFn: () => api.post(apiPath`/families/${familyId}/pgs/calculate`, null, { params }),
    onSuccess: () => client.invalidateQueries({ queryKey }),
  });
  const data = query.data;
  const busy = data?.runs.some((run) => ['queued', 'running'].includes(run.status));
  const versions = new Map(data?.versions.map((version) => [version.id, version]));
  const sources = new Map(data?.sources.map((source) => [source.id, source]));
  const error = family.error || reference.isError || query.error || calculate.error;
  if (family.isPending || (family.data?.projects?.length && reference.isLoading)) {
    return <PageState loading kicker="Exploratory PGS" title="Loading family PGS" message="Loading the family and its project reference." />;
  }
  if (family.error || !family.data) {
    return <FamilyLoadFailure kicker="Exploratory PGS" what="Family" error={family.error}
      notFoundMessage="The requested family is unavailable." onRetry={() => { void family.refetch(); }} />;
  }
  return (
    <div className="page-shell space-y-5">
      <FamilyCaseHeader familyId={familyId} family={family.data} projectId={projectId} kicker="Exploratory PGS"
        assemblyLabel={reference.assemblyName || 'Not linked'} current="pgs"
        assemblyScope={{ name: reference.assemblyName, validated: reference.assemblyValidated, unavailable: reference.isError, onRetry: reference.retry }} />
      <h1>Exploratory polygenic scores</h1>
      <p>Raw additive GT scores on GRCh38. No clinical interpretation or ancestry correction. These values are excluded from clinical sign-out.</p>
      <Link to={routePath`/families/${familyId}`}>Family workspace</Link>
      {error && <div role="alert" className="status-note status-note--error">
        {getErrorMessage(error, 'Could not load the family PGS records.')} <button className="button-secondary" onClick={() => { reference.retry(); void family.refetch(); void query.refetch(); }}>Retry</button>
      </div>}
      {query.isFetching && !data && <p role="status">Loading PGS records…</p>}
      {data && <>
        <section className="surface-card space-y-3">
          <h2>Calculation and QC</h2>
          {data.notices.map((notice) => <p key={notice}>{notice}</p>)}
          <p>WGS source: {data.source_state.replaceAll('_', ' ')}.</p>
          {data.pending_version_ids.length > 0 && <p role="status">{data.pending_version_ids.length} model version(s) await calculation for this family.</p>}
          <button className="form-button" disabled={!data.can_calculate || busy || calculate.isPending || Boolean(error)} onClick={() => calculate.mutate()}>
            {busy ? 'Calculation queued or running' : 'Calculate active models for this family'}
          </button>
          <p>Only an absent record may count as homozygous REF under the recorded full-WGS assumption. No-calls, missing samplecalls, filtered calls, allele mismatches and ambiguous records make the score incomplete.</p>
          {data.runs.map((run) => <p key={run.id}>Run {run.id}: {run.status}{run.error ? ` — ${run.error}` : ''}</p>)}
        </section>
        <section className="surface-card space-y-3">
          <h2>Scores and history</h2>
          {!data.results.length && <p>No scores have been calculated for this family.</p>}
          <div className="overflow-auto"><table className="data-table">
            <thead><tr><th>Individual</th><th>Model / version</th><th>Raw score</th><th>QC</th><th>Source</th><th>Calculated</th></tr></thead>
            <tbody>{data.results.map((result) => {
              const version = versions.get(result.version_id);
              return <tr key={result.id}>
                <td>{result.sample_id}</td><td>{version?.pgs_id} / {version?.version}{version?.archived ? ' (archived)' : ''}</td>
                <td>{result.raw_score == null ? 'Incomplete — no raw score' : result.raw_score.toPrecision(10)}</td>
                <td>{Object.entries(result.qc).map(([key, value]) => `${key.replaceAll('_', ' ')}: ${value}`).join('; ')}</td>
                <td>{result.current_source ? 'Current import' : 'Historical import'}</td><td>{result.created_at}</td>
              </tr>;
            })}</tbody>
          </table></div>
          {data.results.map((result) => {
            const version = versions.get(result.version_id);
            const source = sources.get(result.source_id);
            return <details key={result.id}>
              <summary>Provenance: {result.sample_id} / {version?.pgs_id} / {version?.version} / {result.created_at}</summary>
              <p>Study: {version?.study}. Build: {version?.genome_build}. {version && <a href={version.source_url} target="_blank" rel="noreferrer">Scoring source</a>}</p>
              <p>Weights SHA-256: <code>{version?.weights_sha256}</code>. Resolved weights: <code>{version?.resolved_sha256}</code>.</p>
              <p>WGS: {source?.package_path}/{source?.relative_vcf}. SHA-256: <code>{source?.sha256}</code>.</p>
              <p>Import job: {source?.import_job_id || 'Synchronous import'}. Genotype storage version: {source?.storage_version}. Fingerprint: {source?.storage_fingerprint}. Calculation: {result.calculation_version}.</p>
              <pre className="overflow-auto">{JSON.stringify({ assumptions: result.assumptions, reference: version?.reference, catalog: version?.catalog_metadata }, null, 2)}</pre>
            </details>;
          })}
        </section>
      </>}
    </div>
  );
};
export default FamilyPgsPage;
