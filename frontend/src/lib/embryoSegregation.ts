/**
 * Derived embryo segregation classification at the region of interest (ROI).
 *
 * Given the family's lineage-tagged haplotype blocks over the ROI, the members,
 * and the inheritance model, this reuses the canonical disease-haplotype inference
 * (haplotypeRisk.ts) to classify each EMBRYO as affected/at-risk, carrier,
 * unaffected, or uninformative — and flags the caveats a clinician must see:
 *
 *   - recombinationNearRoi: a haplotype block boundary (crossover) falls inside or
 *     very close to the ROI, so the embryo's haplotype changes across the locus and
 *     the call is uncertain;
 *   - uninformative: no call can be made — either the analysis could not resolve a
 *     disease haplotype at the ROI (no informative markers / greyed lineage), or the
 *     embryo's own haplotype does not cover the ROI on the parental side the call needs
 *     (missing data is never read as "does not carry the risk haplotype");
 *   - sexDependent: X-linked recessive, the embryo's sex is not recorded and its call would
 *     differ between a son and a daughter, so the call assumes neither sex;
 *   - phaseCorrectionsNearRoi: a parent's phase switch the blocks undid lies inside or close
 *     to the ROI. The swap leaves which embryos share a haplotype unchanged, but where the
 *     parent's phase switched is known only to within the children's switches, so the call
 *     across the locus is less certain, and an embryo's own crossover there may be hidden.
 *
 * This is DERIVED FROM THE ANALYSIS — it is not entered by an analyst or user.
 */
import {
  assessSampleHaplotypeRisk,
  inferDiseaseHaplotypes,
  normalizeHaplotypeChrom,
  resolveHaplotypeInheritanceModel,
  type HaplotypeMemberLike,
  type HaplotypeRiskAssessment,
  type HaplotypeRiskRegion,
  type HaplotypeRiskState,
  type HaplotypeSampleLike,
  type HaplotypeSegmentLike,
} from './haplotypeRisk';
import { phaseCorrectionsNearRegion, type HaplotypePhaseCorrection } from './haplotypePhaseCorrections';

/** A crossover within the ROI, or within this flank of either edge, is "close to
 * the ROI" — close enough that it undermines the segregation call at the locus. */
export const ROI_RECOMBINATION_FLANK = 250_000;

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
   * when they differ. The state is then Affected / at risk, or uninformative (see haplotypeRisk). */
  sexDependent: HaplotypeRiskAssessment['sexDependent'];
  /** The parents' phase corrections inside or close to the ROI (see the module comment). */
  phaseCorrectionsNearRoi: HaplotypePhaseCorrection[];
}

const segmentsForSample = (
  samples: HaplotypeSampleLike[],
  sampleId: string,
): HaplotypeSegmentLike[] => samples.find((entry) => entry.sample === sampleId)?.segments ?? [];

/** A block boundary (a change in either lane's value or lineage between adjacent
 * blocks) within the ROI ± flank signals a recombination across/near the locus. */
export const hasRecombinationNearRoi = (
  segments: HaplotypeSegmentLike[],
  region: HaplotypeRiskRegion,
  flank: number = ROI_RECOMBINATION_FLANK,
): boolean => {
  const chrom = normalizeHaplotypeChrom(region.chr);
  const onChrom = segments
    .filter((seg) => !seg.chr || normalizeHaplotypeChrom(seg.chr) === chrom)
    .sort((a, b) => a.start - b.start);
  const lo = region.start - flank;
  const hi = region.end + flank;
  for (let i = 1; i < onChrom.length; i += 1) {
    const prev = onChrom[i - 1];
    const cur = onChrom[i];
    const changed =
      prev.hap1 !== cur.hap1 ||
      prev.hap2 !== cur.hap2 ||
      prev.hap1_lineage !== cur.hap1_lineage ||
      prev.hap2_lineage !== cur.hap2_lineage;
    if (changed && cur.start >= lo && cur.start <= hi) return true;
  }
  return false;
};

export const classifyEmbryosAtRoi = ({
  members,
  samples,
  inheritanceModel,
  region,
  phaseCorrections = [],
}: {
  members: HaplotypeMemberLike[];
  samples: HaplotypeSampleLike[];
  inheritanceModel?: string | null;
  region: HaplotypeRiskRegion;
  phaseCorrections?: HaplotypePhaseCorrection[];
}): EmbryoClassification[] => {
  const model = inferDiseaseHaplotypes({
    samples,
    members,
    inheritanceModel: resolveHaplotypeInheritanceModel(inheritanceModel, members),
    region,
  });
  const correctionsNearRoi = phaseCorrectionsNearRegion(phaseCorrections, region, ROI_RECOMBINATION_FLANK);
  return members
    .filter((member) => String(member.role || '').toLowerCase() === 'embryo')
    .map((member) => {
      const { state, roiNotCovered, sexDependent } = assessSampleHaplotypeRisk({ model, samples, member, region });
      return {
        sampleId: member.sample_id,
        state,
        uninformative: state === 'uninformative' || !model.informative,
        roiNotCovered,
        sexDependent,
        recombinationNearRoi: hasRecombinationNearRoi(segmentsForSample(samples, member.sample_id), region),
        phaseCorrectionsNearRoi: correctionsNearRoi,
      };
    });
};

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
