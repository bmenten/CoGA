import React, { useState } from 'react';
import { Link } from 'react-router';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';
import type {
  AncestryAnalysisOut,
  AncestryCallsetScanOut,
  AncestrySampleOut,
  FamilyAncestryOut,
} from '../../lib/apiSchema.generated';
import AncestryPcaPlot from '../../components/visualizations/AncestryPcaPlot';
import InfoIcon from '../../components/InfoIcon';
import AncestryChoices from './AncestryChoices';

const PCA_HELP =
  'PCA places each individual on fixed axes computed from public reference genomes only. ' +
  'Axis percentages are the share of reference variation each axis explains. Each individual ' +
  'is read from the family’s stored callsets: CoGA measures how much of the reference markers ' +
  'a callset covers (a genome, an exome or only part of them) and fits the axes to the markers ' +
  'it has. Projection needs at least 1,000 usable markers and 95% of the covered markers ' +
  'usable; a panel or a subset of sites is not projected.';

/** The QC counts a projection keeps, by the name the table shows them under. */
const QC_LABELS: Record<string, string> = {
  filtered: 'filtered',
  unfiltered: 'unfiltered, not accepted',
  no_call: 'no call',
  outside_covered: 'outside what the callset covers',
  missing_record: 'no record',
  missing_sample: 'sample not in VCF',
  ambiguous_record: 'ambiguous record',
  allele_mismatch: 'other alleles',
  invalid_gt: 'invalid genotype',
};

/** How a callset was read, by the kind the scan found. */
const KIND_LABELS: Record<string, string> = {
  complete: 'a call at every marker',
  genome: 'genome',
  exome: 'exome',
  regions: 'the regions given',
  partial: 'part of the markers',
};

const ASSUMPTION_LABELS: Record<string, string> = {
  genotypes: 'Genotypes read',
  callset_kinds: 'How a callset is read',
  min_coverage_ratio: 'Minimum coverage ratio',
  complete_share: 'Share of markers with a call for a complete callset',
  exome_mask: 'Exome mask',
  filters: 'Filters',
  min_usable_markers: 'Minimum usable markers',
  min_call_rate: 'Minimum share of covered markers usable',
  excluded: 'Left out',
  projection: 'Projection',
  // A projection from before the callsets were read, of a registered WGS file.
  absent_record_is_hom_ref: 'Absent records taken as homozygous reference',
  allow_unfiltered: 'Unfiltered calls (FILTER=.) accepted',
  min_observed_fraction: 'Minimum share of observed calls',
  missing_calls: 'Missing calls',
  source_limitations: 'Source limitations',
};

const formatAssumption = (value: unknown) =>
  typeof value === 'boolean' ? (value ? 'Yes' : 'No') : String(value);

const percent = (value: number | null | undefined, digits = 0) =>
  value === null || value === undefined ? '—' : `${(value * 100).toFixed(digits)}%`;

/** A coverage ratio: a few calls in thousands of markers still reads as more than none. */
const ratio = (value: number | null | undefined) =>
  percent(value, value !== null && value !== undefined && value > 0 && value < 0.1 ? 1 : 0);

/** Usable markers of those the callset covers; a callset covering none has its calls only. */
const usableOf = (scan: AncestryCallsetScanOut) =>
  scan.covered_markers
    ? `${scan.usable_markers.toLocaleString()} of ${scan.covered_markers.toLocaleString()} (${percent(scan.call_rate, 1)})`
    : `${scan.usable_markers.toLocaleString()} called`;

const excludedCounts = (qc: Record<string, number>) =>
  Object.entries(QC_LABELS)
    .filter(([key]) => (qc[key] ?? 0) > 0)
    .map(([key, label]) => `${label}: ${qc[key].toLocaleString()}`)
    .join('; ') || '—';

const readAs = (scan: AncestryCallsetScanOut) =>
  `${scan.label} · ${KIND_LABELS[scan.kind] ?? scan.kind}`;

/** The share of a sequenced genome's non-reference calls the callset holds where it covers. */
const coverage = (scan: AncestryCallsetScanOut) =>
  scan.kind === 'exome'
    ? `${ratio(scan.exome_ratio)} in the exome`
    : scan.kind === 'regions'
      ? `${ratio(scan.regions_ratio)} in the regions`
      : ratio(scan.coverage_ratio);

const chosen = (sample: AncestrySampleOut) =>
  sample.candidates?.find((scan) => scan.callset === sample.callset);

const PROJECTION_LABELS: Record<AncestrySampleOut['status'], string> = {
  projected: 'Projected',
  insufficient_data: 'Not plotted',
  excluded: 'Left out',
};

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
          <th>Read from</th>
          <th>Coverage</th>
          <th>Usable markers</th>
          <th>Projection</th>
          <th>PC1</th>
          <th>PC2</th>
          <th>PC3</th>
          <th>PC4</th>
          <th>Left out</th>
        </tr>
      </thead>
      <tbody>
        {(analysis.samples ?? []).map((sample) => {
          const scan = chosen(sample);
          return (
            <tr key={sample.sample_id}>
              <td>{sample.sample_id}</td>
              <td>{scan ? readAs(scan) : '—'}</td>
              <td>{scan ? coverage(scan) : '—'}</td>
              <td>
                {scan
                  ? usableOf(scan)
                  : sample.status === 'excluded'
                    ? '—'
                    : percent(sample.call_rate, 1)}
              </td>
              <td>
                {PROJECTION_LABELS[sample.status]}
                {sample.reason ? (
                  <span className="table-subtle ancestry-reason">{sample.reason}</span>
                ) : null}
              </td>
              {[0, 1, 2, 3].map((pc) => (
                <td key={pc}>{sample.pcs?.[pc]?.toFixed(3) ?? '—'}</td>
              ))}
              <td>{excludedCounts(sample.qc ?? {})}</td>
            </tr>
          );
        })}
      </tbody>
    </table>
  </div>
);

/** Every callset that holds an individual, as the scan read it: what it covers and why it was or was not used. */
const CallsetTable: React.FC<{ analysis: AncestryAnalysisOut }> = ({
  analysis,
}) => {
  const rows = (analysis.samples ?? []).flatMap((sample) =>
    (sample.candidates ?? []).map((scan) => ({ sample, scan }))
  );
  if (!rows.length) return null;
  return (
    <details>
      <summary>Callsets per individual</summary>
      <p className="table-subtle">
        A sequenced genome carries the ALT allele at about{' '}
        {Math.round(rows[0].scan.expected_carriers).toLocaleString()} of the
        markers. A callset’s coverage is its non-reference calls against that
        expectation, inside and outside the exome; it decides how an absent
        record is read.
      </p>
      <div className="overflow-auto">
        <table className="data-table ancestry-quality-table">
          <caption>The family’s stored callsets at the reference markers</caption>
          <thead>
            <tr>
              <th>Individual</th>
              <th>Callset</th>
              <th>Read as</th>
              <th>Non-reference calls</th>
              <th>In the exome</th>
              <th>Outside the exome</th>
              <th>Usable markers</th>
              <th>Used</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(({ sample, scan }) => (
              <tr key={`${sample.sample_id}:${scan.callset}`}>
                <td>{sample.sample_id}</td>
                <td>{scan.label}</td>
                <td>{KIND_LABELS[scan.kind] ?? scan.kind}</td>
                <td>
                  {scan.carriers.toLocaleString()} ({ratio(scan.coverage_ratio)})
                </td>
                <td>{ratio(scan.exome_ratio)}</td>
                <td>{ratio(scan.outside_exome_ratio)}</td>
                <td>{usableOf(scan)}</td>
                <td>
                  {sample.callset === scan.callset && sample.status === 'projected'
                    ? 'Projected'
                    : scan.projectable
                      ? 'Usable, not chosen'
                      : 'Not usable'}
                  {scan.reason ? (
                    <span className="table-subtle ancestry-reason">{scan.reason}</span>
                  ) : null}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
};

interface Props {
  familyId: string;
  projectId?: string;
  enabled: boolean;
}

/**
 * The family's exploratory ancestry PCA, a section of its Sample QC: the latest (or a chosen
 * earlier) projection over its own frozen reference axes, the callset each individual was read
 * from and its marker quality, and the calculation. Outside CoGA's intended purpose (TF-01 §4):
 * it is not validated, never read clinically and never part of the sign-out or the signed
 * record, so the section says so before anything else, whatever its data.
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
    // Look again every few seconds while a calculation is queued or running.
    refetchInterval: (q) =>
      (q.state.data?.runs ?? []).some((run) =>
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
  // An answer without its lists is read as empty: the section never takes Sample QC down.
  const runs = data?.runs ?? [];
  const busy = runs.some((run) => ['queued', 'running'].includes(run.status));
  const analysis = data?.analysis;
  const error = query.error || calculate.error;
  const completedRuns = runs.filter((run) => run.status === 'completed');
  // The latest calculation that did not complete, when it is newer than the result shown.
  const latestRun = runs[0];
  const unfinished =
    latestRun && latestRun.status !== 'completed' ? latestRun : undefined;
  return (
    <section
      id="ancestry"
      className="surface-card space-y-3"
      aria-labelledby="family-ancestry-heading"
    >
      <div className="ancestry-heading">
        <div>
          <div className="ancestry-section-head">
            <h2 id="family-ancestry-heading" className="section-title">
              Ancestry (exploratory)
            </h2>
            <InfoIcon name="How the PCA is read" text={PCA_HELP} />
          </div>
          <p className="text-muted">
            Genetic similarity of the family’s individuals to public reference
            populations, read from their stored callsets. It is calculated
            again automatically when the family’s genotypes change.
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
      <p className="status-note status-note--warning" role="note">
        <strong>Exploratory — not for clinical use.</strong> It is not an
        ancestry percentage or an ethnic identity, and it has not been
        validated. It is never part of the sign-out or of the signed record.{' '}
        <Link to="/docs/reference/ancestry-pca">How the PCA is calculated</Link>
      </p>
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
          {(data.notices ?? []).map((notice) => (
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
              {unfinished.trigger === 'automatic'
                ? 'Automatic calculation'
                : 'Calculation requested'}{' '}
              {new Date(unfinished.requested_at).toLocaleString()}:{' '}
              {unfinished.status}
              {unfinished.error ? ` — ${unfinished.error}` : ''}
            </p>
          )}
          {!analysis && (
            <p>
              No PCA projection has been calculated for this family yet. It is
              calculated automatically once its genotypes are imported.
              {data.reference
                ? ' The plot shows the reference populations.'
                : ''}
            </p>
          )}
          {analysis &&
            (!analysis.current_genotypes || !analysis.current_reference) && (
              <p role="status" className="status-note status-note--warning">
                Earlier projection:{' '}
                {!analysis.current_genotypes
                  ? analysis.storage_version
                    ? 'the family’s genotypes changed since it was calculated. '
                    : 'it read a registered WGS file, before the stored callsets were read. '
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
                    {new Date(run.requested_at).toLocaleString()} ·{' '}
                    {run.trigger === 'automatic' ? 'automatic' : 'requested'}
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
          {analysis && <CallsetTable analysis={analysis} />}
          {data.can_choose && analysis && (
            <AncestryChoices
              familyId={familyId}
              projectId={projectId}
              data={data}
              queryKey={queryKey}
            />
          )}
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
                    {analysis.current_genotypes
                      ? 'current genotypes'
                      : 'earlier genotypes'}{' '}
                    · calculation <code>{analysis.calculation_version}</code>
                    {analysis.source_id ? (
                      <>
                        {' '}
                        · WGS source record <code>{analysis.source_id}</code>
                      </>
                    ) : null}
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
