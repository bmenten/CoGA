import React from 'react';
import MultiplePregnancyNote from './MultiplePregnancyNote';
import { ANALYSIS_PARAM } from '../../lib/familyAnalyses';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath, raw } from '../../lib/apiPath';
import type { CniptSampleResultOut, FamilyCniptOut } from '../../lib/apiSchema.generated';
import { routePath } from '../../lib/routePath';
import {
  CNIPT_CLAIM_NOTE,
  CNIPT_VERDICT_CHIP,
  CNIPT_VERDICT_LABEL,
  cniptTargetLabel,
  orderCniptTargets,
  OUTSIDE_CLAIM,
  percent,
} from './cniptResult';

const PlasmaSummary: React.FC<{ sample: CniptSampleResultOut }> = ({ sample }) => {
  const { headline, others } = orderCniptTargets(sample.targets);
  const flagged = others.filter((target) => target.verdict !== 'low_risk');
  const lowRisk = others.length - flagged.length;
  const provisional = sample.bounds.filter((bound) => bound.provisional);
  return (
    <div className="report-section">
      <h3 className="report-subheading">
        Maternal plasma {sample.sample_id} · fetal fraction {percent(sample.fetal_fraction.effective)} · fetal sex{' '}
        {sample.fetal_sex}
      </h3>
      {sample.status === 'no_result' ? (
        <p className="report-paragraph">No result: {sample.no_result_reasons.join('; ')}.</p>
      ) : null}
      <ul className="report-signed-list" aria-label={`Result of ${sample.sample_id}`}>
        {[...headline, ...flagged].map((target) => (
          <li key={target.target}>
            {cniptTargetLabel(target.target)}
            {target.claimed ? '' : ` (${OUTSIDE_CLAIM})`}:{' '}
            <span className={CNIPT_VERDICT_CHIP[target.verdict]}>{CNIPT_VERDICT_LABEL[target.verdict]}</span>
            {target.reasons.length ? ` — ${target.reasons.join('; ')}` : ''}
          </li>
        ))}
        {lowRisk ? (
          <li>
            {lowRisk === 1 ? 'The other autosome' : `The ${lowRisk} other autosomes`} ({OUTSIDE_CLAIM}):{' '}
            <span className={CNIPT_VERDICT_CHIP.low_risk}>{CNIPT_VERDICT_LABEL.low_risk}</span>
          </li>
        ) : null}
      </ul>
      <p className="report-paragraph">{CNIPT_CLAIM_NOTE}</p>
      {provisional.length ? (
        <p className="status-note status-note--warning">
          The result rests on provisional bounds, which QA has not set:{' '}
          {provisional.map((bound) => bound.label.replace(/^cNIPT: /, '')).join('; ')}. Signing out asks for an
          acknowledgement.
        </p>
      ) : null}
    </div>
  );
};

/**
 * A conventional NIPT family's result on its report, as the cNIPT page shows it: what the
 * sign-out freezes in the record's `cnipt` section (signed_record_cnipt).
 */
const CniptReportSummary: React.FC<{ familyId: string; projectId?: string | null; analysis: string | null }> = ({
  familyId,
  projectId,
  analysis,
}) => {
  const { data, isLoading, isError } = useQuery<FamilyCniptOut>({
    queryKey: ['family', familyId, 'cnipt', projectId ?? undefined, analysis],
    queryFn: async () => {
      // The pregnancy's conventional NIPT (its key), named of a family that holds one per pregnancy
      // of several.
      const params: Record<string, string> = {};
      if (analysis) params[ANALYSIS_PARAM] = analysis;
      if (projectId) params.project_id = projectId;
      return (await api.get(apiPath`/families/${familyId}/cnipt`, { params })).data as FamilyCniptOut;
    },
  });
  const linkParams = new URLSearchParams();
  if (projectId) linkParams.set('project_id', projectId);
  if (analysis) linkParams.set(ANALYSIS_PARAM, analysis);
  const project = linkParams.toString() ? `?${linkParams.toString()}` : '';
  return (
    <section className="surface-card report-signed-checks" aria-label="Conventional NIPT result">
      <h2 className="report-audit-heading">Conventional NIPT result</h2>
      {isLoading ? (
        <p className="report-paragraph">Loading the conventional NIPT result…</p>
      ) : isError || !data ? (
        <p className="report-paragraph" role="alert">
          The conventional NIPT result could not be loaded. Sign-out reads it again and freezes it, or names it as
          not captured.
        </p>
      ) : (
        <>
          <p className="report-paragraph">
            The screening result of each maternal plasma, by the rule of z-score and fetal-fraction concordance.
            Signing out freezes it with its findings, its bounds and its inputs.{' '}
            <Link className="no-print" to={routePath`/families/${familyId}/cnipt${raw(project)}`}>
              Open the cNIPT page
            </Link>
          </p>
          <MultiplePregnancyNote note={data.multiple_pregnancy_note} fetuses={data.fetuses} />
          {data.samples.map((sample) => (
            <PlasmaSummary key={sample.sample_id} sample={sample} />
          ))}
        </>
      )}
    </section>
  );
};

export default CniptReportSummary;
