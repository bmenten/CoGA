/**
 * The embryo calls at the region of interest (ROI), as the family page shows them.
 *
 * The backend makes them (backend/app/services/pgt_embryo_calls.py, served by
 * GET /families/{id}/pgt-analysis), and a sign-out of the family freezes the same calls into
 * its signed record: one implementation, from the stored haplotype blocks. Each embryo reads
 * affected/at-risk, carrier, unaffected, or uninformative, with the caveats a clinician must
 * see:
 *
 *   - recombinationNearRoi: a haplotype block boundary (crossover) falls inside or very close
 *     to the ROI, so the embryo's haplotype changes across the locus and the call is uncertain;
 *   - uninformative: no call can be made — either the analysis could not resolve a disease
 *     haplotype at the ROI (no informative markers / greyed lineage), or the embryo's own
 *     haplotype does not cover the ROI on the parental side the call needs (missing data is
 *     never read as "does not carry the risk haplotype");
 *   - sexDependent: X-linked recessive, the embryo's sex is not recorded and its call would
 *     differ between a son and a daughter, so the call assumes neither sex;
 *   - phaseCorrectionsNearRoi: a parent's phase switch the blocks undid lies inside or close
 *     to the ROI. The swap leaves which embryos share a haplotype unchanged, but where the
 *     parent's phase switched is known only to within the children's switches, so the call
 *     across the locus is less certain, and an embryo's own crossover there may be hidden;
 *   - qcVerdict: the embryo's phasing QC at the ROI checked against the thresholds
 *     (backend/app/services/pgt_embryo_qc.py). A QC that does not meet them, or that was not
 *     counted, is a warning beside the call: the call stands and the analyst decides.
 *
 * This is DERIVED FROM THE ANALYSIS — it is not entered by an analyst or user.
 */
import type { PgtAnalysisOut, PgtEmbryoQcFindingOut, PgtEmbryoQcVerdictOut } from './apiSchema.generated';
import type { HaplotypeRiskAssessment, HaplotypeRiskState } from './haplotypeRisk';
import type { HaplotypePhaseCorrection } from './haplotypePhaseCorrections';

export interface EmbryoClassification {
  sampleId: string;
  /** affected_or_at_risk | carrier | unaffected_non_carrier | uninformative */
  state: HaplotypeRiskState;
  /** A crossover falls inside/near the ROI — the call across the locus is uncertain. */
  recombinationNearRoi: boolean;
  /** No call can be made at the ROI (see `roiNotCovered` for which reason). */
  uninformative: boolean;
  /** Uninformative because this embryo's own haplotype does not cover the ROI on a parental
   * side the call needs (missing data), not because no disease haplotype was resolved. */
  roiNotCovered: boolean;
  /** X-linked recessive with the sex not recorded: the call a son and a daughter would each get,
   * when they differ. The state is then Affected / at risk, or uninformative. */
  sexDependent: HaplotypeRiskAssessment['sexDependent'];
  /** The parents' phase corrections inside or close to the ROI (see the module comment). */
  phaseCorrectionsNearRoi: HaplotypePhaseCorrection[];
  /** The phasing QC at the ROI against the thresholds; null when the backend gave none. */
  qcVerdict: PgtEmbryoQcVerdictOut | null;
  /** Why no QC was counted at the ROI, for a verdict of `not_counted`. */
  qcNotCountedReason: string;
}

/** Why an embryo's phasing QC at the ROI was not counted, in the words the record uses too. */
export const qcNotCountedReason = (markersTruncated: boolean): string =>
  markersTruncated
    ? 'the ROI holds more sites than the marker review reads'
    : 'the embryo is not recorded as a child of the couple';

/**
 * Each called embryo of the family's PGT analysis, by sample id. An embryo the analysis did
 * not call (the family has no ROI, or no haplotypes were read) has no entry, as before.
 */
export const embryoClassificationsFromAnalysis = (analysis: PgtAnalysisOut): Map<string, EmbryoClassification> =>
  new Map(
    analysis.embryos.flatMap((embryo): [string, EmbryoClassification][] =>
      embryo.call === null
        ? []
        : [
            [
              embryo.sample,
              {
                sampleId: embryo.sample,
                state: embryo.call,
                recombinationNearRoi: embryo.recombinations.length > 0,
                uninformative: embryo.call === 'uninformative' || !analysis.disease_haplotype_resolved,
                roiNotCovered: embryo.roi_not_covered,
                sexDependent: embryo.sex_dependent
                  ? { ifMale: embryo.sex_dependent.if_male, ifFemale: embryo.sex_dependent.if_female }
                  : null,
                phaseCorrectionsNearRoi: analysis.phase_corrections,
                qcVerdict: embryo.qc_verdict ?? null,
                qcNotCountedReason: qcNotCountedReason(analysis.inputs.markers_truncated),
              },
            ],
          ],
    ),
  );

const STATE_LABELS: Record<HaplotypeRiskState, string> = {
  affected_or_at_risk: 'Affected / at risk',
  carrier: 'Carrier',
  unaffected_non_carrier: 'Unaffected',
  uninformative: 'Uninformative',
};

export const segregationStateLabel = (state: HaplotypeRiskState): string => STATE_LABELS[state];

type SexDependentCalls = NonNullable<EmbryoClassification['sexDependent']>;

// --- The phasing QC at the ROI against its thresholds ------------------------------------

/** A threshold rate as a percentage, without trailing zeros: 0.02 → "2%", 0.0125 → "1.25%". */
export const formatQcRate = (rate: number): string => `${Number((rate * 100).toFixed(3))}%`;

/** A rate above its maximum, with the decimals that set it apart: 0.0202 → "2.02%", never "2%". */
const formatRateAbove = (rate: number, maximum: number): string => {
  for (let digits = 1; digits < 4; digits += 1) {
    const shown = Number((rate * 100).toFixed(digits));
    if (shown !== Number((maximum * 100).toFixed(digits))) return `${shown}%`;
  }
  return `${Number((rate * 100).toFixed(4))}%`;
};

const count = (value: number, one: string, many: string): string =>
  `${value.toLocaleString()} ${value === 1 ? one : many}`;

/** One measure that does not meet its threshold: "6 informative sites (fewer than 50)". */
export const describeQcFinding = (finding: PgtEmbryoQcFindingOut): string => {
  switch (finding.measure) {
    case 'informative_sites':
      return `${count(finding.value, 'informative site', 'informative sites')} (fewer than ${finding.threshold.toLocaleString()})`;
    case 'mendel_rate':
      return `Mendelian error rate ${formatRateAbove(finding.value, finding.threshold)} (above ${formatQcRate(finding.threshold)})`;
    case 'paternal_markers':
      return `${count(finding.value, 'marker', 'markers')} on the father's side (fewer than ${finding.threshold.toLocaleString()})`;
    case 'maternal_markers':
      return `${count(finding.value, 'marker', 'markers')} on the mother's side (fewer than ${finding.threshold.toLocaleString()})`;
  }
};

/** The measures that do not meet their thresholds, in the verdict's order. */
export const describeQcFindings = (verdict: Pick<PgtEmbryoQcVerdictOut, 'findings'>): string =>
  verdict.findings.map(describeQcFinding).join('; ');

/** Which parents' markers the verdict checked: the sides the call reads. */
export const describeQcSides = (sides: PgtEmbryoQcVerdictOut['sides']): string => {
  const paternal = sides.includes('paternal');
  const maternal = sides.includes('maternal');
  if (paternal && maternal) return "markers checked on both parents' sides";
  if (paternal) return "markers checked on the father's side";
  if (maternal) return "markers checked on the mother's side";
  return "markers not checked: the call reads neither parent's side";
};

/**
 * The family page's warning beside an embryo's call when its phasing QC at the ROI does not
 * meet its thresholds, or was not counted; null when it meets them. The call stands either
 * way: the warning tells the analyst to check it.
 */
export const qcVerdictWarning = (verdict: PgtEmbryoQcVerdictOut | null, notCountedReason: string): string | null => {
  if (!verdict) return null;
  if (verdict.status === 'not_counted') {
    return (
      `The phasing QC at the ROI was not counted (${notCountedReason}), so this call was not checked ` +
      'against the QC thresholds. Review the ROI markers.'
    );
  }
  if (verdict.status !== 'warning' || !verdict.findings.length) return null;
  return (
    `The phasing QC at the ROI does not meet its thresholds: ${describeQcFindings(verdict)}. ` +
    'The call stands: review the ROI markers before relying on it.'
  );
};

/** The warning on an embryo whose call depends on its unrecorded sex: both calls. */
export const sexUnknownWarning = ({ ifMale, ifFemale }: SexDependentCalls): string => {
  const call = (state: HaplotypeRiskState): string => segregationStateLabel(state).toLowerCase();
  return (
    `This embryo's sex is not recorded, and the call depends on it: ${call(ifMale)} if male, ` +
    `${call(ifFemale)} if female. Record the sex to resolve it.`
  );
};
