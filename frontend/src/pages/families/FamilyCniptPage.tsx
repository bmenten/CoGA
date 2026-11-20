import React, { useMemo } from 'react';
import { Link, useLocation, useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import { raw, routePath } from '../../lib/routePath';
import type { ApiFamilyRecord } from '../../lib/apiTypes';
import type { CniptSampleResultOut, CniptTargetOut, FamilyCniptOut } from '../../lib/apiSchema.generated';
import { formatResolvedReferenceLabel, useFamilyReference } from '../../lib/reference';
import FamilyCaseHeader from './FamilyCaseHeader';
import PageState from '../../components/PageState';
import FamilyLoadFailure from '../../components/FamilyLoadFailure';
import InfoIcon from '../../components/InfoIcon';
import GenomeWorkspaceLink from './GenomeWorkspaceLink';
import {
  CNIPT_VERDICT_CHIP,
  CNIPT_VERDICT_LABEL,
  boundValue,
  CNIPT_CLAIM_NOTE,
  cniptTargetLabel,
  orderCniptTargets,
  OUTSIDE_CLAIM,
  percent,
} from './cniptResult';

const VerdictChip: React.FC<{ verdict: CniptTargetOut['verdict'] }> = ({ verdict }) => (
  <span className={CNIPT_VERDICT_CHIP[verdict]}>{CNIPT_VERDICT_LABEL[verdict]}</span>
);

const Reasons: React.FC<{ reasons: readonly string[] }> = ({ reasons }) =>
  reasons.length ? (
    <ul className="cnipt-reasons">
      {reasons.map((reason) => (
        <li key={reason}>{reason}</li>
      ))}
    </ul>
  ) : (
    <span className="table-subtle">No finding.</span>
  );

const TargetRows: React.FC<{ targets: readonly CniptTargetOut[] }> = ({ targets }) => (
  <>
    {targets.map((target) => (
      <tr key={target.target}>
        <th scope="row">
          {cniptTargetLabel(target.target)}
          {target.claimed ? null : <div className="table-subtle">{OUTSIDE_CLAIM}</div>}
        </th>
        <td>
          <VerdictChip verdict={target.verdict} />
        </td>
        <td>
          <Reasons reasons={target.reasons} />
        </td>
      </tr>
    ))}
  </>
);

const formatMetric = (key: string, value: number): string =>
  key.includes('reads') || key.includes('bins')
    ? value.toLocaleString('en-US')
    : String(Number(value.toPrecision(4)));

/** One maternal plasma's result, its sample and its findings. */
const CniptSampleResult: React.FC<{
  familyId: string;
  projectId?: string | null;
  sample: CniptSampleResultOut;
}> = ({ familyId, projectId, sample }) => {
  const { headline, others } = useMemo(() => orderCniptTargets(sample.targets), [sample.targets]);
  const flaggedOthers = others.filter((target) => target.verdict !== 'low_risk');
  const project = projectId ? `&project_id=${encodeURIComponent(projectId)}` : '';
  const ff = sample.fetal_fraction;
  const anyProvisional = sample.bounds.some((bound) => bound.provisional);
  return (
    <div className="space-y-4" data-testid={`cnipt-sample-${sample.sample_id}`}>
      <section className="surface-card space-y-3" aria-label={`Result of ${sample.sample_id}`}>
        <div className="page-header">
          <div className="space-y-1">
            <h2 className="section-title">Result · {sample.sample_id}</h2>
            <p className="table-subtle">
              Automatic screening result from ccnv&apos;s output ({sample.qc_file}), by the rule z-score and
              fetal-fraction concordance. A screening result: a high risk calls for confirmatory diagnostic testing.
            </p>
          </div>
        </div>
        {sample.status === 'no_result' ? (
          <div className="cnipt-no-result" role="status">
            <VerdictChip verdict="no_result" /> <Reasons reasons={sample.no_result_reasons} />
          </div>
        ) : null}
        <div className="data-table-shell data-table-shell--fit">
          <table className="cnipt-targets-table">
            <thead>
              <tr>
                <th scope="col">Target</th>
                <th scope="col">Result</th>
                <th scope="col">Why</th>
              </tr>
            </thead>
            <tbody>
              <TargetRows targets={headline} />
              <TargetRows targets={flaggedOthers} />
            </tbody>
          </table>
        </div>
        <p className="status-note" role="note">
          {CNIPT_CLAIM_NOTE}
        </p>
        <details className="cnipt-other-autosomes">
          <summary>
            Every other autosome ({others.length - flaggedOthers.length} low risk
            {flaggedOthers.length ? `, ${flaggedOthers.length} listed above` : ''}; {OUTSIDE_CLAIM})
          </summary>
          <div className="data-table-shell data-table-shell--fit">
            <table className="cnipt-targets-table">
              <tbody>
                <TargetRows targets={others} />
              </tbody>
            </table>
          </div>
        </details>
      </section>

      <section className="surface-card space-y-3" aria-label={`Sample ${sample.sample_id}`}>
        <h2 className="section-title">Maternal plasma</h2>
        <dl className="cnipt-facts">
          <div>
            <dt>Fetal fraction</dt>
            <dd>
              <strong>{percent(ff.effective)}</strong> effective (ccnv)
              <span className="table-subtle">
                {' · '}active {percent(ff.active)}
                {ff.active_se != null ? ` ± ${percent(ff.active_se)}` : ''}
                {ff.active_source ? ` (${ff.active_source})` : ''}
                {ff.ffy != null ? ` · FFY ${percent(ff.ffy)}` : ''}
                {ff.ffx != null ? ` · FFX ${percent(ff.ffx)}` : ''}
                {ff.ffa != null ? ` · FFA ${percent(ff.ffa)}` : ''}
                {ff.ff_size != null ? ` · size ${percent(ff.ff_size)}` : ''}
              </span>
            </dd>
          </div>
          <div>
            <dt>Fetal sex</dt>
            <dd>
              {sample.fetal_sex} · sex-chromosome call {sample.sca.call}
              {sample.sca.confidence != null ? ` (confidence ${percent(sample.sca.confidence, 2)})` : ''}
            </dd>
          </div>
          {sample.gestation ? (
            <div>
              <dt>Pregnancy</dt>
              <dd>{sample.gestation}</dd>
            </div>
          ) : null}
          <div>
            <dt>Resolution</dt>
            <dd>
              {sample.limits_of_detection.statement}
              <span className="table-subtle">
                {sample.limits_of_detection.lod_ff_3mb != null
                  ? ` · LoD 3 Mb at FF ${percent(sample.limits_of_detection.lod_ff_3mb)}`
                  : ''}
                {sample.limits_of_detection.lod_ff_35mb != null
                  ? ` · LoD arm-level at FF ${percent(sample.limits_of_detection.lod_ff_35mb)}`
                  : ''}
              </span>
            </dd>
          </div>
          {Object.keys(sample.read_metrics).length ? (
            <div>
              <dt>Reads and bins</dt>
              <dd className="table-subtle">
                {Object.entries(sample.read_metrics)
                  .map(([key, value]) => `${key.replace(/_/g, ' ')} ${formatMetric(key, value)}`)
                  .join(' · ')}
              </dd>
            </div>
          ) : null}
        </dl>
        {sample.warnings.length ? (
          <ul className="cnipt-warnings" aria-label="Warnings">
            {sample.warnings.map((warning) => (
              <li key={warning} className="status-note status-note--warning">
                {warning}
              </li>
            ))}
          </ul>
        ) : null}
      </section>

      <section className="surface-card space-y-3" aria-label={`Findings of ${sample.sample_id}`}>
        <div className="paraphase-section-head">
          <h2 className="section-title">Findings ({sample.findings.length})</h2>
          <InfoIcon
            name="About the findings"
            text="Every segment ccnv called, and every segment whose |z-score| reaches the grey zone. A segment spanning half its chromosome is read as an aneuploidy of it; a shorter one as a segmental CNV."
          />
        </div>
        {sample.findings.length ? (
          <div className="data-table-shell">
            <table className="cnipt-findings-table">
              <thead>
                <tr>
                  <th scope="col">Region</th>
                  <th scope="col">Size</th>
                  <th scope="col">Kind</th>
                  <th scope="col">ccnv</th>
                  <th scope="col">log2 ratio</th>
                  <th scope="col">z-score</th>
                  <th scope="col">Implied FF</th>
                  <th scope="col">Origin</th>
                  <th scope="col">Result</th>
                </tr>
              </thead>
              <tbody>
                {sample.findings.map((finding) => (
                  <tr key={`${finding.chrom}:${finding.start}`}>
                    <td>
                      <Link
                        to={routePath`/families/${familyId}/chromosome/${finding.chrom}?start=${finding.start}&end=${finding.end}${raw(project)}`}
                      >
                        {`chr${finding.chrom}:${finding.start.toLocaleString('en-US')}–${finding.end.toLocaleString('en-US')}`}
                      </Link>
                    </td>
                    <td>{`${finding.size_mb} Mb`}</td>
                    <td>{finding.kind === 'chromosome' ? `aneuploidy (${finding.direction})` : `segmental ${finding.direction}`}</td>
                    <td>
                      {finding.ccnv_call ?? 'not called'}
                      {finding.discordant ? (
                        <span className="table-subtle"> · discordant{finding.discordance_reason ? ` (${finding.discordance_reason})` : ''}</span>
                      ) : null}
                    </td>
                    <td>{Number(finding.ratio.toPrecision(3))}</td>
                    <td>{finding.zscore != null ? Number(finding.zscore.toPrecision(3)) : 'n/a'}</td>
                    <td>
                      {percent(finding.implied_ff)}
                      {finding.implied_ff_ratio != null ? (
                        <span className="table-subtle">{` (${finding.implied_ff_ratio} × FF)`}</span>
                      ) : null}
                    </td>
                    <td>{finding.origin_call ?? 'n/a'}</td>
                    <td>
                      <VerdictChip verdict={finding.verdict} />
                      <Reasons reasons={finding.reasons} />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="table-subtle">No segment reaches the grey zone, and ccnv called none.</p>
        )}
      </section>

      <section className="surface-card space-y-3" aria-label="Bounds of the rule">
        <div className="paraphase-section-head">
          <h2 className="section-title">Bounds of the rule</h2>
          <InfoIcon
            name="About the bounds"
            text="QA sets each bound on the QC thresholds page (profile Conventional NIPT), each change with its reason. Until QA sets one, the provisional value applies."
          />
        </div>
        {anyProvisional ? (
          <p className="status-note status-note--warning">
            Provisional bounds apply: QA and the laboratory director have not set them yet.
          </p>
        ) : null}
        <div className="data-table-shell data-table-shell--fit">
          <table className="cnipt-bounds-table">
            <thead>
              <tr>
                <th scope="col">Bound</th>
                <th scope="col">Warning</th>
                <th scope="col">Error</th>
                <th scope="col">Set by</th>
              </tr>
            </thead>
            <tbody>
              {sample.bounds.map((bound) => (
                <tr key={bound.key}>
                  <th scope="row">{bound.label}</th>
                  <td>{boundValue(bound.warn_value, bound.unit)}</td>
                  <td>{boundValue(bound.error_value, bound.unit)}</td>
                  <td>{bound.provisional ? 'provisional' : 'QA'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <p className="table-subtle">
          A segment spanning at least {percent(sample.rule.whole_chromosome_share, 0)} of its chromosome is read as an
          aneuploidy of it. Inconclusive origins: {sample.rule.maternal_or_mosaic_origins.join(', ')}.
        </p>
      </section>
    </div>
  );
};

const FamilyCniptPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const location = useLocation();
  const projectIdParam = useMemo(
    () => new URLSearchParams(location.search).get('project_id') || undefined,
    [location.search],
  );
  const {
    data: family,
    isLoading: familyLoading,
    isError: familyFailed,
    error: familyError,
    refetch: refetchFamily,
  } = useQuery<ApiFamilyRecord>({
    queryKey: ['family', familyId],
    queryFn: async () => (await api.get(apiPath`/families/${familyId}`)).data as ApiFamilyRecord,
  });
  const {
    assemblyName,
    assemblyValidated,
    projectId: resolvedProjectId,
    isLoading: referenceLoading,
    isError: referenceFailed,
    retry: retryReference,
  } = useFamilyReference(family?.projects, projectIdParam);
  const {
    data: result,
    isLoading: resultLoading,
    isError: resultFailed,
    error: resultError,
    refetch: refetchResult,
  } = useQuery<FamilyCniptOut>({
    queryKey: ['family', familyId, 'cnipt', resolvedProjectId],
    queryFn: async () =>
      (
        await api.get(apiPath`/families/${familyId}/cnipt`, {
          params: resolvedProjectId ? { project_id: resolvedProjectId } : undefined,
        })
      ).data as FamilyCniptOut,
    enabled: Boolean(familyId && (!family?.projects?.length || !referenceLoading)),
  });

  if (familyLoading || resultLoading || (family?.projects?.length && referenceLoading)) {
    return (
      <PageState
        loading
        kicker="cNIPT"
        title="Loading the conventional NIPT result"
        message="Reading ccnv's output of the maternal plasma."
      />
    );
  }
  if (familyFailed || resultFailed) {
    return (
      <FamilyLoadFailure
        kicker="cNIPT"
        what={familyFailed ? 'Family' : 'Conventional NIPT result'}
        error={familyFailed ? familyError : resultError}
        notFoundMessage="This family holds no ccnv output of a conventional NIPT."
        onRetry={() => {
          if (familyFailed) void refetchFamily();
          if (resultFailed) void refetchResult();
        }}
      />
    );
  }
  if (!family || !result) {
    return <PageState kicker="cNIPT" title="Family not found" message="The family could not be resolved." />;
  }
  const referenceLabel = formatResolvedReferenceLabel({ assemblyName, isError: referenceFailed }, 'Not linked');
  return (
    <div className="page-shell family-cnipt-page space-y-6">
      <FamilyCaseHeader
        assemblyScope={{
          name: assemblyName,
          validated: assemblyValidated,
          unavailable: referenceFailed,
          onRetry: retryReference,
        }}
        kicker="cNIPT"
        familyId={family.family_id}
        family={family}
        projectId={resolvedProjectId}
        assemblyLabel={referenceLabel}
        current="cnipt"
        views={
          <GenomeWorkspaceLink
            to={routePath`/families/${family.family_id}/genome${raw(
              resolvedProjectId ? `?project_id=${encodeURIComponent(resolvedProjectId)}` : '',
            )}`}
            className="family-module-link family-module-link--view hover:no-underline"
            label="Open the genome overview"
          >
            Genome
          </GenomeWorkspaceLink>
        }
      />
      {result.samples.map((sample) => (
        <CniptSampleResult
          key={sample.sample_id}
          familyId={family.family_id}
          projectId={resolvedProjectId}
          sample={sample}
        />
      ))}
    </div>
  );
};

export default FamilyCniptPage;
