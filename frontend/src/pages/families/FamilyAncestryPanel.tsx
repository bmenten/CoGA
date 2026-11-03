import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type {
  AncestryAnalysisOut,
  FamilyAncestryOut,
} from '../../lib/apiSchema.generated';
import AncestryPcaPlot from '../../components/visualizations/AncestryPcaPlot';
import InfoIcon from '../../components/InfoIcon';

const PCA_HELP =
  'PCA places each individual on fixed axes computed from public reference genomes only. ' +
  'Axis percentages are the share of reference variation each axis explains. Projection ' +
  'needs at least 1,000 usable markers, 95% of the markers usable and, when absent records ' +
  'are assumed reference, 20% of the markers observed as calls; an individual below these ' +
  'thresholds is not plotted.';

/** The QC counts a projection keeps, by the name the table shows them under. */
const QC_LABELS: Record<string, string> = {
  filtered: 'filtered',
  no_call: 'no call',
  missing_record: 'no record',
  missing_sample: 'sample not in VCF',
  ambiguous_record: 'ambiguous record',
  allele_mismatch: 'other alleles',
  invalid_gt: 'invalid genotype',
};

const ASSUMPTION_LABELS: Record<string, string> = {
  absent_record_is_hom_ref: 'Absent records taken as homozygous reference',
  allow_unfiltered: 'Unfiltered calls (FILTER=.) accepted',
  min_usable_markers: 'Minimum usable markers',
  min_call_rate: 'Minimum share of usable markers',
  min_observed_fraction: 'Minimum share of observed calls',
  genotypes: 'Genotypes read',
  missing_calls: 'Missing calls',
  source_limitations: 'Source limitations',
};

const formatAssumption = (value: unknown) =>
  typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);

const excludedCounts = (qc: Record<string, number>) =>
  Object.entries(QC_LABELS)
    .filter(([key]) => (qc[key] ?? 0) > 0)
    .map(([key, label]) => `${label}: ${qc[key].toLocaleString()}`)
    .join('; ') || '—';

const SampleTable: React.FC<{ analysis: AncestryAnalysisOut }> = ({
  analysis,
}) => (
  <div className="overflow-auto">
    <table className="data-table ancestry-quality-table">
      <caption>
        Projections and marker quality of the family’s individuals
      </caption>
      <thead>
        <tr>
          <th>Individual</th>
          <th>Projection</th>
          <th>Observed calls</th>
          <th>Assumed REF</th>
          <th>Usable markers</th>
          <th>PC1</th>
          <th>PC2</th>
          <th>PC3</th>
          <th>PC4</th>
          <th>Left out</th>
        </tr>
      </thead>
      <tbody>
        {analysis.samples.map((sample) => (
          <tr key={sample.sample_id}>
            <td>{sample.sample_id}</td>
            <td>
              {sample.status === 'projected'
                ? 'Projected'
                : 'Insufficient data — not plotted'}
            </td>
            <td>
              {(
                (sample.qc.called ?? 0) + (sample.qc.called_unfiltered ?? 0)
              ).toLocaleString()}
            </td>
            <td>{(sample.qc.assumed_ref ?? 0).toLocaleString()}</td>
            <td>{(sample.call_rate * 100).toFixed(1)}%</td>
            {[0, 1, 2, 3].map((pc) => (
              <td key={pc}>{sample.pcs?.[pc]?.toFixed(3) ?? '—'}</td>
            ))}
            <td>{excludedCounts(sample.qc)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

interface Props {
  familyId: string;
  projectId?: string;
  enabled: boolean;
}

/**
 * The family's exploratory ancestry PCA: the latest (or a chosen earlier) projection over its
 * own frozen reference axes, the marker quality of each individual, and the calculation.
 */
const FamilyAncestryPanel: React.FC<Props> = ({
  familyId,
  projectId,
  enabled,
}) => {
  const client = useQueryClient();
  const [runId, setRunId] = useState('');
  const queryKey = ['family-ancestry', familyId, projectId];
  const params = {
    ...(projectId ? { project_id: projectId } : {}),
    ...(runId ? { run_id: runId } : {}),
  };
  const query = useQuery({
    queryKey: [...queryKey, runId],
    enabled,
    queryFn: async () =>
      (
        await api.get<FamilyAncestryOut>(
          apiPath`/families/${familyId}/ancestry`,
          { params }
        )
      ).data,
    // A calculation takes minutes: look again every few seconds while one is queued or running.
    refetchInterval: (q) =>
      q.state.data?.runs.some((run) =>
        ['queued', 'running'].includes(run.status)
      )
        ? 5000
        : false,
  });
  const calculate = useMutation({
    mutationFn: () =>
      api.post(apiPath`/families/${familyId}/ancestry/calculate`, null, {
        params: projectId ? { project_id: projectId } : {},
      }),
    onSuccess: async () => {
      setRunId('');
      await client.invalidateQueries({ queryKey });
    },
  });
  const data = query.data;
  const busy = data?.runs.some((run) =>
    ['queued', 'running'].includes(run.status)
  );
  const analysis = data?.analysis;
  const error = query.error || calculate.error;
  const completedRuns =
    data?.runs.filter((run) => run.status === 'completed') ?? [];
  // The latest calculation that did not complete, when it is newer than the result shown.
  const latestRun = data?.runs[0];
  const unfinished =
    latestRun && latestRun.status !== 'completed' ? latestRun : undefined;
  return (
    <section
      className="surface-card space-y-3"
      aria-labelledby="family-ancestry-heading"
    >
      <div className="ancestry-heading">
        <div>
          <div className="ancestry-section-head">
            <h2 id="family-ancestry-heading" className="section-title">
              Genetic similarity (PCA)
            </h2>
            <InfoIcon name="How the PCA is read" text={PCA_HELP} />
          </div>
          <p className="text-muted">
            The family’s individuals beside public reference populations.
          </p>
        </div>
        <button
          type="button"
          className="form-button"
          disabled={
            !data?.can_calculate || busy || calculate.isPending || !enabled
          }
          onClick={() => calculate.mutate()}
        >
          {busy || calculate.isPending
            ? 'PCA projection queued or running'
            : 'Calculate PCA projection'}
        </button>
      </div>
      {error && (
        <div role="alert" className="status-note status-note--error">
          {getErrorMessage(error, 'Could not load ancestry.')}{' '}
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              calculate.reset();
              void query.refetch();
            }}
          >
            Retry ancestry
          </button>
        </div>
      )}
      {query.isFetching && !data && (
        <p role="status">Loading ancestry reference and projections…</p>
      )}
      {data && (
        <>
          {data.notices.map((notice) => (
            <p key={notice} className="status-note">
              {notice}
            </p>
          ))}
          {unfinished && (
            <p
              role={busy ? 'status' : undefined}
              className={
                busy ? 'status-note' : 'status-note status-note--warning'
              }
            >
              Calculation requested{' '}
              {new Date(unfinished.requested_at).toLocaleString()}:{' '}
              {unfinished.status}
              {unfinished.error ? ` — ${unfinished.error}` : ''}
            </p>
          )}
          {!analysis && (
            <p>
              No PCA projection has been calculated for this family.
              {data.reference
                ? ' The plot shows the reference populations.'
                : ''}
            </p>
          )}
          {analysis &&
            (!analysis.current_source || !analysis.current_reference) && (
              <p role="status" className="status-note status-note--warning">
                Earlier projection:{' '}
                {!analysis.current_source
                  ? 'the WGS source has changed or is unavailable. '
                  : ''}
                {!analysis.current_reference
                  ? 'It was calculated with an earlier reference model. '
                  : ''}
                It is shown on its own reference axes.
              </p>
            )}
          {completedRuns.length > 0 && (
            <label className="ancestry-history">
              Projection
              <select value={runId} onChange={(e) => setRunId(e.target.value)}>
                <option value="">Latest completed projection</option>
                {completedRuns.map((run) => (
                  <option key={run.id} value={run.id}>
                    {new Date(run.requested_at).toLocaleString()}
                  </option>
                ))}
              </select>
            </label>
          )}
          {data.reference && (
            <AncestryPcaPlot
              key={`${data.reference.sha256}:${analysis?.run_id || ''}`}
              reference={data.reference}
              samples={analysis?.samples || []}
            />
          )}
          {analysis && <SampleTable analysis={analysis} />}
          {data.reference && (
            <details>
              <summary>Reference and calculation</summary>
              <p>
                <a
                  href={data.reference.source_url}
                  target="_blank"
                  rel="noreferrer"
                >
                  {data.reference.name}
                </a>{' '}
                · {data.reference.genome_build}
              </p>
              <p>{data.reference.method}</p>
              <p>
                Reference SHA-256: <code>{data.reference.sha256}</code>
              </p>
              {analysis && (
                <>
                  <p>
                    Calculated {new Date(analysis.created_at).toLocaleString()}{' '}
                    ·{' '}
                    {analysis.current_source
                      ? 'current WGS source'
                      : 'earlier WGS source'}{' '}
                    · calculation <code>{analysis.calculation_version}</code> ·
                    source record <code>{analysis.source_id}</code>
                  </p>
                  <dl className="ancestry-assumptions">
                    {Object.entries(ASSUMPTION_LABELS)
                      .filter(
                        ([key]) =>
                          key in analysis.assumptions &&
                          analysis.assumptions[key] !== ''
                      )
                      .map(([key, label]) => (
                        <React.Fragment key={key}>
                          <dt>{label}</dt>
                          <dd>{formatAssumption(analysis.assumptions[key])}</dd>
                        </React.Fragment>
                      ))}
                  </dl>
                </>
              )}
            </details>
          )}
        </>
      )}
    </section>
  );
};

export default FamilyAncestryPanel;
