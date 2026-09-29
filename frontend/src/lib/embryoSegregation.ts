/**
 * Derived embryo segregation classification at the region of interest (ROI).
 *
 * Given the family's lineage-tagged haplotype blocks over the ROI, the members,
 * and the inheritance model, this reuses the canonical disease-haplotype inference
 * (haplotypeRisk.ts) to classify each EMBRYO as affected/at-risk, carrier,
 * unaffected, or uninformative — and flags two caveats a clinician must see:
 *
 *   - recombinationNearRoi: a haplotype block boundary (crossover) falls inside or
 *     very close to the ROI, so the embryo's haplotype changes across the locus and
 *     the call is uncertain;
 *   - uninformative: no call can be made — either the analysis could not resolve a
 *     disease haplotype at the ROI (no informative markers / greyed lineage), or the
 *     embryo's own haplotype does not cover the ROI on the parental side the call needs
 *     (missing data is never read as "does not carry the risk haplotype").
 *
 * This is DERIVED FROM THE ANALYSIS — it is not entered by an analyst or user.
 */
import {
  assessSampleHaplotypeRisk,
  inferDiseaseHaplotypes,
  normalizeHaplotypeChrom,
  resolveHaplotypeInheritanceModel,
  type HaplotypeMemberLike,
  type HaplotypeRiskRegion,
  type HaplotypeRiskState,
  type HaplotypeSampleLike,
  type HaplotypeSegmentLike,
} from './haplotypeRisk';

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
}: {
  members: HaplotypeMemberLike[];
  samples: HaplotypeSampleLike[];
  inheritanceModel?: string | null;
  region: HaplotypeRiskRegion;
}): EmbryoClassification[] => {
  const model = inferDiseaseHaplotypes({
    samples,
    members,
    inheritanceModel: resolveHaplotypeInheritanceModel(inheritanceModel, members),
    region,
  });
  return members
    .filter((member) => String(member.role || '').toLowerCase() === 'embryo')
    .map((member) => {
      const { state, roiNotCovered } = assessSampleHaplotypeRisk({ model, samples, member, region });
      return {
        sampleId: member.sample_id,
        state,
        uninformative: state === 'uninformative' || !model.informative,
        roiNotCovered,
        recombinationNearRoi: hasRecombinationNearRoi(segmentsForSample(samples, member.sample_id), region),
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
