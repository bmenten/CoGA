/**
 * The conventional NIPT's result as the cNIPT page shows it (`GET /families/{id}/cnipt`,
 * backend `cnipt_result.py`): its labels, tones and the order its targets are read in.
 */

import type { CniptTargetOut } from '../../lib/apiSchema.generated';

/** The family metadata `analysis_type` of a conventional NIPT family. */
export const CONVENTIONAL_NIPT_ANALYSIS_TYPE = 'conventional_nipt';

export const isConventionalNipt = (metadata: unknown): boolean =>
  (metadata as { analysis_type?: string } | null | undefined)?.analysis_type === CONVENTIONAL_NIPT_ANALYSIS_TYPE;

export type CniptVerdict = CniptTargetOut['verdict'];

export const CNIPT_VERDICT_LABEL: Record<CniptVerdict, string> = {
  high_risk: 'High risk',
  inconclusive: 'Inconclusive',
  low_risk: 'Low risk',
  no_result: 'No result',
  not_assessed: 'Not assessed',
};

/** A verdict as a signed record holds it, in the page's words; one this view does not know as it is. */
export const cniptVerdictLabel = (verdict: string | null): string =>
  verdict === null ? 'not in the record' : (CNIPT_VERDICT_LABEL[verdict as CniptVerdict] ?? verdict);

/** One verdict looks the same wherever it is shown: the shared chip tones. */
export const CNIPT_VERDICT_CHIP: Record<CniptVerdict, string> = {
  high_risk: 'table-chip table-chip--critical',
  inconclusive: 'table-chip table-chip--warning',
  low_risk: 'table-chip table-chip--success',
  no_result: 'table-chip table-chip--neutral',
  not_assessed: 'table-chip table-chip--neutral',
};

/** The targets a result reads first: chromosomes 21, 18 and 13, the sex chromosomes, segmental CNVs. */
export const HEADLINE_TARGETS = ['21', '18', '13', 'sex_chromosomes', 'segmental'] as const;

export const cniptTargetLabel = (target: string): string => {
  if (target === 'sex_chromosomes') return 'Sex chromosomes';
  if (target === 'segmental') return 'Segmental CNVs';
  if (target === '21' || target === '18' || target === '13') return `Chromosome ${target} (T${target})`;
  return `Chromosome ${target}`;
};

/** The headline targets in their order, then every other autosome in its own. */
export const orderCniptTargets = (
  targets: readonly CniptTargetOut[],
): { headline: CniptTargetOut[]; others: CniptTargetOut[] } => {
  const byTarget = new Map(targets.map((target) => [target.target, target]));
  const headline = HEADLINE_TARGETS.map((key) => byTarget.get(key)).filter(
    (target): target is CniptTargetOut => target !== undefined,
  );
  const shown = new Set<string>(HEADLINE_TARGETS);
  const others = targets.filter((target) => !shown.has(target.target));
  return { headline, others };
};

/** A fraction (0–1) as a percentage. */
export const percent = (fraction: number | null | undefined, digits = 1): string =>
  fraction == null ? 'n/a' : `${(fraction * 100).toFixed(digits)}%`;

/** A bound's value in its unit: a fraction as a percentage, the rest as it is. */
export const boundValue = (value: number | null | undefined, unit: string): string => {
  if (value == null) return '—';
  if (unit === 'fraction') return percent(value, value < 0.1 ? 1 : 0);
  return `${Number(value.toPrecision(4))}${unit === 'Mb' ? ' Mb' : unit === '× FF' ? ' × FF' : ''}`;
};
