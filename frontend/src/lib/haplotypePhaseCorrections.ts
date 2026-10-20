/**
 * The parents' phase switches the haplotype blocks undid (`families.metadata.
 * haplotype_phase_corrections`, written by the haplotype import).
 *
 * A switch error in a parent's phasing makes all the couple's children seem to cross
 * over at the same place on that parent's side. Where (nearly) all of them switched
 * together, the blocks swap that parent's two haplotypes back from there, so the
 * children's spurious crossovers disappear. The genotypes stay as called; the views
 * mark where the swap is. Which corrections lie near the region of interest, where every
 * embryo is warned of them, the backend reads with the embryo calls
 * (backend/app/services/pgt_embryo_calls.py, `phase_corrections_near`).
 */

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
