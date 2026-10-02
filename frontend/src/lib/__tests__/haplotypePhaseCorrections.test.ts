import { describe, expect, it } from 'vitest';
import {
  describePhaseCorrection,
  phaseCorrectionsFromMetadata,
  phaseCorrectionsNearRegion,
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

  it('finds the corrections whose switches lie within the flank of a region', () => {
    const near = { chr: 'chr7', start: 30_500_000, end: 30_600_000 };
    expect(phaseCorrectionsNearRegion([correction], near, 250_000)).toEqual([correction]);
    expect(phaseCorrectionsNearRegion([correction], { ...near, start: 31_000_000, end: 31_100_000 }, 250_000)).toEqual([]);
    // The phase may have switched up to 2 Mb before the correction: a ROI there is near it too.
    expect(
      phaseCorrectionsNearRegion([correction], { ...near, start: 28_000_000, end: 28_100_000 }, 250_000),
    ).toEqual([correction]);
    expect(
      phaseCorrectionsNearRegion([correction], { ...near, start: 27_000_000, end: 27_100_000 }, 250_000),
    ).toEqual([]);
    expect(phaseCorrectionsNearRegion([correction], { ...near, chr: '8' }, 250_000)).toEqual([]);
    expect(phaseCorrectionsNearRegion([correction], { ...near, chr: null }, 250_000)).toEqual([]);
  });
});
