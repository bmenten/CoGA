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
 *     counted, is a warning beside the call: the call stands and the analyst decides;
 *   - mixedHomologs: the embryo's markers at the ROI mix both homologs of a parent the call
 *     reads, as where it carries both (a trisomy), which its blocks cannot show: it is not
 *     called (uninformative), the one QC measure that changes a call.
 *
 * This is DERIVED FROM THE ANALYSIS — it is not entered by an analyst or user.
 */
import type { PgtAnalysisOut, PgtEmbryoQcFindingOut, PgtEmbryoQcVerdictOut } from './apiSchema.generated';

type PgtParentSide = 'paternal' | 'maternal';
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
  /** The haplotype blocks were built for another pedigree or other sexes (CLIN-66): every
   * embryo is uninformative until the phased genotypes are imported again. */
  blocksOutdated: boolean;
  /** The parental sides whose markers at the ROI mix both of that parent's homologs: the
   * embryo may carry both (a trisomy), which its blocks cannot show, so it is not called. */
  mixedHomologs: MixedHomologs[];
  /** The discordance above which a side's markers are read as mixing both homologs. */
  maxMarkerDiscordance: number | null;
}

/** A parental side whose markers at the ROI mix both of that parent's homologs. */
export interface MixedHomologs {
  side: PgtParentSide;
  /** The share of the side's markers off one homolog, or two either side of one crossover. */
  discordance: number | null;
  markers: number | null;
}

/** Why the embryos are uninformative while their haplotype blocks are outdated (CLIN-66), in
 * the words the family page and the signed record share. */
export const BLOCKS_OUTDATED_WARNING =
  "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.";

/** Why an embryo's phasing QC at the ROI was not counted, in the words the record uses too. */
export const qcNotCountedReason = (markersTruncated: boolean): string =>
  markersTruncated
    ? 'the ROI and its flanks hold more sites than the marker review reads'
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
                blocksOutdated: analysis.inputs.blocks_outdated ?? false,
                mixedHomologs: (embryo.mixed_homologs ?? []).map((side) => ({
                  side,
                  discordance: embryo.qc ? (side === 'paternal' ? embryo.qc.paternal_discordance : embryo.qc.maternal_discordance) ?? null : null,
                  markers: embryo.qc ? (side === 'paternal' ? embryo.qc.paternal_markers : embryo.qc.maternal_markers) ?? null : null,
                })),
                maxMarkerDiscordance: analysis.rules.qc_thresholds.max_marker_discordance ?? null,
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

/** Where a parent's markers were counted: " left of the ROI", " right of the ROI", or nothing. */
const flankText = (flank: PgtEmbryoQcFindingOut['flank'] | undefined): string =>
  flank === 'left' ? ' left of the ROI' : flank === 'right' ? ' right of the ROI' : '';

/**
 * One measure that does not meet its threshold: "6 informative sites (fewer than 50)", "3 markers
 * on the father's side left of the ROI (fewer than 6)".
 */
export const describeQcFinding = (finding: PgtEmbryoQcFindingOut): string => {
  switch (finding.measure) {
    case 'informative_sites':
      return `${count(finding.value, 'informative site', 'informative sites')} (fewer than ${finding.threshold.toLocaleString()})`;
    case 'mendel_rate':
      return `Mendelian error rate ${formatRateAbove(finding.value, finding.threshold)} (above ${formatQcRate(finding.threshold)})`;
    case 'paternal_markers':
      return `${count(finding.value, 'marker', 'markers')} on the father's side${flankText(finding.flank)} (fewer than ${finding.threshold.toLocaleString()})`;
    case 'maternal_markers':
      return `${count(finding.value, 'marker', 'markers')} on the mother's side${flankText(finding.flank)} (fewer than ${finding.threshold.toLocaleString()})`;
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

/**
 * Why an embryo whose markers at the ROI mix both homologs of a parent is not called, in the
 * words the family page and the signed record share: "This embryo's markers at the ROI mix both
 * of the mother's homologs (47.5% of 120 markers off one homolog, or two either side of one
 * crossover; above 10%): …". With both parents' sides mixed, each share names its parent.
 */
export const mixedHomologsWarning = (mixed: MixedHomologs[], maximum: number | null): string => {
  const parent = (side: PgtParentSide): string => (side === 'paternal' ? "the father's" : "the mother's");
  const measured = mixed
    .filter((entry) => entry.discordance !== null && entry.markers !== null)
    .map(
      (entry) =>
        `${mixed.length > 1 ? `${parent(entry.side)} ` : ''}${formatQcRate(entry.discordance!)} of ${count(entry.markers!, 'marker', 'markers')}`,
    );
  const detail = measured.length
    ? ` (${measured.join(', ')} off one homolog, or two either side of one crossover${maximum !== null ? `; above ${formatQcRate(maximum)}` : ''})`
    : '';
  const whose = mixed.length > 1 ? "the father's and the mother's" : mixed.length ? parent(mixed[0].side) : "a parent's";
  return (
    `This embryo's markers at the ROI mix both of ${whose} homologs${detail}: ` +
    'it may carry both, as in a trisomy of this chromosome, which its haplotype blocks cannot show, so no call is made. ' +
    'Read its copy-number and APCAD tracks.'
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
