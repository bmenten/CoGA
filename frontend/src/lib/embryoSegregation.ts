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
 *     across the locus is less certain, and an embryo's own crossover there may be hidden.
 *
 * This is DERIVED FROM THE ANALYSIS — it is not entered by an analyst or user.
 */
import type { PgtAnalysisOut } from './apiSchema.generated';
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
}

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

/** The warning on an embryo whose call depends on its unrecorded sex: both calls. */
export const sexUnknownWarning = ({ ifMale, ifFemale }: SexDependentCalls): string => {
  const call = (state: HaplotypeRiskState): string => segregationStateLabel(state).toLowerCase();
  return (
    `This embryo's sex is not recorded, and the call depends on it: ${call(ifMale)} if male, ` +
    `${call(ifFemale)} if female. Record the sex to resolve it.`
  );
};
