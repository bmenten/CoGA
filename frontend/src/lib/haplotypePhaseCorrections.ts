/**
 * The parents' phase switches the haplotype blocks undid (`families.metadata.
 * haplotype_phase_corrections`, written by the haplotype import).
 *
 * A switch error in a parent's phasing makes all the couple's children seem to cross
 * over at the same place on that parent's side. Where (nearly) all of them switched
 * together, the blocks swap that parent's two haplotypes back from there, so the
 * children's spurious crossovers disappear. The genotypes stay as called; the views
 * mark where the swap is.
 */
import { normalizeHaplotypeChrom } from './haplotypeRisk';

export interface HaplotypePhaseCorrection {
  /** The parent whose phase was swapped back. */
  parent: string;
  side: 'father' | 'mother';
  chr: string;
  /** The parent's haplotypes are swapped from here on. */
  position: number;
  /** The children's switches lie between `position` and `end`. */
  end: number;
  children_switching: number;
  children: number;
}

/** How far before a correction the parent's phase may already have switched: each child's
 * switch is read where its own run of agreeing sites begins, which noise delays, and the
 * swap starts at the earliest of them (the cluster span, `PHASE_SWITCH_CLUSTER_SPAN`). */
export const PHASE_SWITCH_UNCERTAINTY = 2_000_000;

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

/** The well-formed corrections in a family's metadata. */
export const phaseCorrectionsFromMetadata = (
  metadata: Record<string, unknown> | null | undefined,
): HaplotypePhaseCorrection[] => {
  const value = metadata?.haplotype_phase_corrections;
  if (!Array.isArray(value)) return [];
  return value.flatMap((item) => {
    if (!isRecord(item)) return [];
    const { parent, side, chr, position, end, children_switching, children } = item;
    if (typeof parent !== 'string' || typeof chr !== 'string' || typeof position !== 'number') return [];
    return [
      {
        parent,
        side: side === 'mother' ? 'mother' : 'father',
        chr,
        position,
        end: typeof end === 'number' ? end : position,
        children_switching: typeof children_switching === 'number' ? children_switching : 0,
        children: typeof children === 'number' ? children : 0,
      },
    ];
  });
};

const formatMb = (position: number): string => `${(position / 1_000_000).toFixed(2)} Mb`;

/** What a correction means, for a tooltip. */
export const describePhaseCorrection = (correction: HaplotypePhaseCorrection): string => {
  const parent = correction.side === 'mother' ? "mother's" : "father's";
  const where =
    correction.end > correction.position
      ? `between ${formatMb(correction.position)} and ${formatMb(correction.end)}`
      : `at ${formatMb(correction.position)}`;
  return (
    `Phase corrected: ${correction.children_switching} of ${correction.children} children switched ` +
    `together on the ${parent} side ${where}, a switch in the ${parent} phasing rather than ` +
    `crossovers. The ${parent} two haplotypes are swapped from ${formatMb(correction.position)}.`
  );
};

/** The corrections on the region's chromosome whose switch may lie within `flank` of it:
 * anywhere from `PHASE_SWITCH_UNCERTAINTY` before the correction to its children's last switch. */
export const phaseCorrectionsNearRegion = (
  corrections: HaplotypePhaseCorrection[],
  region: { chr?: string | null; start: number; end: number },
  flank: number,
): HaplotypePhaseCorrection[] => {
  if (!region.chr) return [];
  const chrom = normalizeHaplotypeChrom(region.chr);
  return corrections.filter(
    (correction) =>
      normalizeHaplotypeChrom(correction.chr) === chrom &&
      correction.end >= region.start - flank &&
      correction.position - PHASE_SWITCH_UNCERTAINTY <= region.end + flank,
  );
};
