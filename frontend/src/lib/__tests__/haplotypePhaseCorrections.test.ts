import { describe, expect, it } from 'vitest';
import {
  describePhaseCorrection,
  phaseCorrectionsFromMetadata,
  type HaplotypePhaseCorrection,
} from '../haplotypePhaseCorrections';

const correction: HaplotypePhaseCorrection = {
  parent: 'FATHER1',
  side: 'father',
  chr: '7',
  position: 30_000_000,
  end: 30_400_000,
  children_switching: 5,
  children: 5,
};

describe('haplotype phase corrections', () => {
  it('reads the well-formed corrections of a family', () => {
    expect(
      phaseCorrectionsFromMetadata({
        haplotype_phase_corrections: [{ ...correction }, { parent: 'FATHER1' }, 'junk'],
      }),
    ).toEqual([correction]);
    expect(phaseCorrectionsFromMetadata({})).toEqual([]);
    expect(phaseCorrectionsFromMetadata(undefined)).toEqual([]);
  });

  it('says how many children switched together and where the haplotypes are swapped', () => {
    expect(describePhaseCorrection(correction)).toBe(
      "Phase corrected: 5 of 5 children switched together on the father's side between 30.00 Mb and " +
        "30.40 Mb, a switch in the father's phasing rather than crossovers. The father's two haplotypes " +
        'are swapped from 30.00 Mb.',
    );
  });
});
