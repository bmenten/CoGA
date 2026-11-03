import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type { FamilyAncestryOut } from '../../lib/apiSchema.generated';
import AncestryPcaPlot from '../../components/visualizations/AncestryPcaPlot';
import InfoTip from '../../components/InfoTip';

const ANCESTRY_HELP = 'PCA compares genetic similarity with public reference populations on fixed axes; it does not measure ancestry percentages or ethnic identity. Axis percentages show explained variance. Projection requires at least 1,000 usable markers and 95% usable calls. Missing calls remain visible in QC; individuals below these thresholds are not plotted. Center view focuses on a family individual once a usable projection is available.';

interface Props {
  familyId: string;
  projectId?: string;
  enabled: boolean;
}

const FamilyAncestryPanel: React.FC<Props> = ({ familyId, projectId, enabled }) => {
  const client = useQueryClient();
  const [runId, setRunId] = useState('');
  const queryKey = ['family-ancestry', familyId, projectId];
  const params = { ...(projectId ? { project_id: projectId } : {}), ...(runId ? { run_id: runId } : {}) };
  const query = useQuery({
    queryKey: [...queryKey, runId], enabled,
    queryFn: async () => (await api.get<FamilyAncestryOut>(apiPath`/families/${familyId}/ancestry`, { params })).data,
    refetchInterval: (q) => q.state.data?.runs.some((run) => ['queued', 'running'].includes(run.status)) ? 2000 : false,
  });
  const calculate = useMutation({
    mutationFn: () => api.post(apiPath`/families/${familyId}/ancestry/calculate`, null,
      { params: projectId ? { project_id: projectId } : {} }),
    onSuccess: async () => { setRunId(''); await client.invalidateQueries({ queryKey }); },
  });
  const data = query.data;
  const busy = data?.runs.some((run) => ['queued', 'running'].includes(run.status));
  const analysis = data?.analysis;
  const error = query.error || calculate.error;
  return <section className="surface-card space-y-3" aria-labelledby="family-ancestry-heading">
    <div className="ancestry-heading">
      <div>
        <div className="analysis-title">
          <h2 id="family-ancestry-heading">Genetic ancestry · PCA</h2>
          <InfoTip label={ANCESTRY_HELP} interactiveChild>
            <button type="button" className="analysis-help" aria-label="About genetic ancestry PCA">?</button>
          </InfoTip>
        </div>
        <p className="text-muted">Compare family individuals with public reference populations.</p>
      </div>
      <button className="form-button" disabled={!data?.can_calculate || busy || calculate.isPending || !enabled}
        onClick={() => calculate.mutate()}>
        {busy || calculate.isPending ? 'Ancestry calculation queued or running' : 'Calculate ancestry for this family'}
      </button>
    </div>
    {error && <div role="alert" className="status-note status-note--error">
      {getErrorMessage(error, 'Could not load ancestry.')} <button className="button-secondary" onClick={() => { calculate.reset(); void query.refetch(); }}>Retry ancestry</button>
    </div>}
    {query.isFetching && !data && <p role="status">Loading ancestry reference and projections…</p>}
    {data && <>
      {data.notices.filter((notice) => !notice.startsWith('Genetic similarity to public reference populations;')).map((notice) => <p key={notice} className="text-muted">{notice}</p>)}
      {!analysis && <p>No ancestry projection has been calculated for this family.{data.reference ? ' The plot shows the reference populations.' : ''}</p>}
      {analysis && (!analysis.current_source || !analysis.current_reference) && <p role="status" className="status-note status-note--warning">
        Historical projection: {!analysis.current_source ? 'the WGS source has changed or is unavailable. ' : ''}
        {!analysis.current_reference ? 'A previous reference model is shown. ' : ''}The original reference axes are retained below.
      </p>}
      {data.runs.some((run) => run.status === 'completed') && <label className="ancestry-history">Projection history
        <select value={runId} onChange={(e) => setRunId(e.target.value)}>
          <option value="">Latest completed analysis</option>
          {data.runs.filter((run) => run.status === 'completed').map((run) => <option key={run.id} value={run.id}>
            {new Date(run.requested_at).toLocaleString()}
          </option>)}
        </select>
      </label>}
      {data.reference && <AncestryPcaPlot key={`${data.reference.sha256}:${analysis?.run_id || ''}`} reference={data.reference} samples={analysis?.samples || []} />}
      {analysis && <div className="overflow-auto"><table className="data-table ancestry-quality-table">
        <caption>Individual ancestry projections and marker quality</caption>
        <thead><tr><th>Individual</th><th>Projection</th><th>Usable SNPs</th><th>PC1</th><th>PC2</th><th>PC3</th><th>PC4</th><th>QC</th></tr></thead>
        <tbody>{analysis.samples.map((sample) => <tr key={sample.sample_id}>
          <td>{sample.sample_id}</td><td>{sample.status === 'projected' ? 'Projected' : 'Insufficient data — not plotted'}</td>
          <td>{(sample.call_rate * 100).toFixed(1)}%</td>
          {[0, 1, 2, 3].map((pc) => <td key={pc}>{sample.pcs?.[pc]?.toFixed(3) ?? '—'}</td>)}
          <td>{Object.entries(sample.qc).map(([name, count]) => `${name.replaceAll('_', ' ')}: ${count}`).join('; ')}</td>
        </tr>)}</tbody>
      </table></div>}
      {data.runs.filter((run) => run.status !== 'completed').map((run) => <p key={run.id} role={['queued', 'running'].includes(run.status) ? 'status' : undefined}>
        Ancestry calculation: {run.status}{run.error ? ` — ${run.error}` : ''}
      </p>)}
      {data.reference && <details>
        <summary>Ancestry reference and provenance</summary>
        <p><a href={data.reference.source_url} target="_blank" rel="noreferrer">{data.reference.name}</a> · {data.reference.genome_build}</p>
        <p>{data.reference.method}</p>
        <p>Reference SHA-256: <code>{data.reference.sha256}</code>.</p>
        {analysis && <>
          <p>Calculated: {new Date(analysis.created_at).toLocaleString()} · {analysis.current_source ? 'Current WGS source' : 'Historical WGS source'}.</p>
          <p>Source record: <code>{analysis.source_id}</code> · Calculation: <code>{analysis.calculation_version}</code>.</p>
          <pre className="overflow-auto">{JSON.stringify(analysis.assumptions, null, 2)}</pre>
        </>}
      </details>}
    </>}
  </section>;
};

export default FamilyAncestryPanel;
