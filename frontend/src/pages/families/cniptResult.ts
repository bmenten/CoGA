/**
 * The conventional NIPT's result as the cNIPT page shows it (`GET /families/{id}/cnipt`,
 * backend `cnipt_result.py`): its labels, tones and the order its targets are read in.
 */

import type { CniptTargetOut } from '../../lib/apiSchema.generated';
import { holdsAnalysis } from '../../lib/familyAnalyses';

/** The analysis type of a conventional NIPT, among a family's analysis types (`familyAnalysisTypes`). */
export const CONVENTIONAL_NIPT_ANALYSIS_TYPE = 'conventional_nipt';

/** Whether a family holds a conventional NIPT (it may hold a monogenic NIPT beside it). */
export const isConventionalNipt = (metadata: unknown): boolean =>
  holdsAnalysis(metadata, CONVENTIONAL_NIPT_ANALYSIS_TYPE);

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

/**
 * What the intended purpose claims (TF-01 §3.7): the trisomies 21, 18 and 13 and the
 * sex-chromosome aneuploidies. The result says it of each target (`claimed`); the other autosomes
 * and the segmental CNVs are shown outside the validated claim.
 */
export const CNIPT_CLAIM_NOTE =
  'The validated claim covers trisomies 21, 18 and 13 and the sex-chromosome aneuploidies. The other autosomes and the segmental CNVs are shown outside it: screen them as findings outside the claim.';

/** What a target outside the claim adds to its label. */
export const OUTSIDE_CLAIM = 'outside the validated claim';

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

/**
 * A bound's value in its unit: a fraction as a percentage, with the decimals (up to two) its value
 * holds, and one at least below 10 % (4.0 %, 99 %, 99.5 %); the rest as it is.
 */
export const boundValue = (value: number | null | undefined, unit: string): string => {
  if (value == null) return '—';
  if (unit === 'fraction') {
    const held = [0, 1].find((digits) => Math.abs(Number((value * 100).toFixed(digits)) - value * 100) < 1e-9) ?? 2;
    return percent(value, value < 0.1 ? Math.max(1, held) : held);
  }
  return `${Number(value.toPrecision(4))}${unit === 'Mb' ? ' Mb' : unit === '× FF' ? ' × FF' : ''}`;
};
