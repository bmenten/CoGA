import { describe, expect, it } from 'vitest';
import {
  classifyEmbryosAtRoi,
  hasRecombinationNearRoi,
  type EmbryoClassification,
} from '../embryoSegregation';
import type { HaplotypeMemberLike, HaplotypeSampleLike } from '../haplotypeRisk';

const region = { chr: '1', start: 1_000_000, end: 1_001_000 };

const seg = (
  hap1: string,
  hap2: string,
  hap1_lineage = 'paternal',
  hap2_lineage = 'maternal',
  start = 0,
  end = 2_000_000,
) => ({ chr: '1', start, end, hap1, hap2, hap1_lineage, hap2_lineage });

const byId = (rows: EmbryoClassification[]) => Object.fromEntries(rows.map((r) => [r.sampleId, r]));

describe('classifyEmbryosAtRoi (dominant)', () => {
  const members: HaplotypeMemberLike[] = [
    { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'E_RISK', role: 'embryo', affected: false, sex: 'female' },
    { sample_id: 'E_CLEAR', role: 'embryo', affected: false, sex: 'female' },
  ];
  const samples: HaplotypeSampleLike[] = [
    { sample: 'FATHER', segments: [seg('0', '1', 'paternal', 'paternal')] },
    { sample: 'PROBAND', segments: [seg('1', '0')] }, // shares paternal:1 with father -> disease hap
    { sample: 'E_RISK', segments: [seg('1', '1')] }, // inherited paternal:1 -> at risk
    { sample: 'E_CLEAR', segments: [seg('0', '1')] }, // inherited paternal:0 -> unaffected
  ];

  it('classifies each embryo by the shared dominant haplotype', () => {
    const out = byId(classifyEmbryosAtRoi({ members, samples, inheritanceModel: 'AD', region }));
    expect(Object.keys(out).sort()).toEqual(['E_CLEAR', 'E_RISK']);
    expect(out.E_RISK.state).toBe('affected_or_at_risk');
    expect(out.E_CLEAR.state).toBe('unaffected_non_carrier');
    expect(out.E_RISK.recombinationNearRoi).toBe(false);
    expect(out.E_RISK.uninformative).toBe(false);
  });

  it('calls an embryo with no haplotype over the ROI uninformative, never unaffected', () => {
    // The disease haplotype resolves (paternal 1), but these embryos have no block at the
    // ROI: one has blocks only in the flank the ROI view fetches, one none at all. They
    // used to read "Unaffected", the badge of an embryo seen not to carry it.
    const withMissing: HaplotypeMemberLike[] = [
      ...members,
      { sample_id: 'E_FLANK', role: 'embryo', affected: false, sex: 'female' },
      { sample_id: 'E_NONE', role: 'embryo', affected: false, sex: 'female' },
    ];
    const withMissingSamples: HaplotypeSampleLike[] = [
      ...samples,
      {
        sample: 'E_FLANK',
        segments: [
          seg('0', '1', 'paternal', 'maternal', 0, 900_000),
          seg('0', '1', 'paternal', 'maternal', 1_100_000, 2_000_000),
        ],
      },
    ];
    const out = byId(
      classifyEmbryosAtRoi({ members: withMissing, samples: withMissingSamples, inheritanceModel: 'AD', region }),
    );
    for (const id of ['E_FLANK', 'E_NONE']) {
      expect(out[id].state).toBe('uninformative');
      expect(out[id].uninformative).toBe(true);
      // The reason is this embryo's missing data, not an unresolved disease haplotype.
      expect(out[id].roiNotCovered).toBe(true);
    }
    // The embryos with data at the ROI keep their calls.
    expect(out.E_RISK.state).toBe('affected_or_at_risk');
    expect(out.E_CLEAR.state).toBe('unaffected_non_carrier');
    expect(out.E_CLEAR.roiNotCovered).toBe(false);
  });

  it('flags uninformative when the disease haplotype cannot be resolved', () => {
    // A single affected member with a homozygous block is ambiguous -> uninformative.
    const lone: HaplotypeMemberLike[] = [
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'E1', role: 'embryo', affected: false, sex: 'female' },
    ];
    const loneSamples: HaplotypeSampleLike[] = [
      { sample: 'PROBAND', segments: [seg('1', '1')] },
      { sample: 'E1', segments: [seg('1', '0')] },
    ];
    const out = byId(classifyEmbryosAtRoi({ members: lone, samples: loneSamples, inheritanceModel: 'AD', region }));
    expect(out.E1.state).toBe('uninformative');
    expect(out.E1.uninformative).toBe(true);
    // No disease haplotype to call against: that, not the embryo's data, is the reason.
    expect(out.E1.roiNotCovered).toBe(false);
  });
});

describe("classifyEmbryosAtRoi (X-linked recessive, the embryos' sex not recorded)", () => {
  const xRegion = { chr: 'X', start: 1_000_000, end: 1_001_000 };
  // Outside the pseudo-autosomal regions, as the backend marks the block.
  const xSeg = (hap1: string, hap2: string) => ({ ...seg(hap1, hap2), chr: 'X', hemizygous_in_males: true });
  // The affected son resolves the mother's risk X (maternal 1). The trio builder never
  // confirms a paternal X side, hence '?'.
  const members: HaplotypeMemberLike[] = [
    { sample_id: 'SON', role: 'proband', affected: true, sex: 'male' },
    { sample_id: 'E_RISK', role: 'embryo', affected: false, sex: 'unknown' },
    { sample_id: 'E_CLEAR', role: 'embryo', affected: false, sex: 'unknown' },
  ];
  const samples: HaplotypeSampleLike[] = [
    { sample: 'SON', segments: [xSeg('?', '1')] },
    { sample: 'E_RISK', segments: [xSeg('?', '1')] },
    { sample: 'E_CLEAR', segments: [xSeg('?', '0')] },
  ];

  it('calls the embryo with the maternal risk haplotype at risk, with both calls, not a carrier', () => {
    const out = byId(classifyEmbryosAtRoi({ members, samples, inheritanceModel: 'XLR', region: xRegion }));
    expect(out.E_RISK.state).toBe('affected_or_at_risk');
    expect(out.E_RISK.sexDependent).toEqual({ ifMale: 'affected_or_at_risk', ifFemale: 'carrier' });
    expect(out.E_RISK.uninformative).toBe(false);
    // The other maternal X: unaffected whatever the sex.
    expect(out.E_CLEAR.state).toBe('unaffected_non_carrier');
    expect(out.E_CLEAR.sexDependent ?? null).toBeNull();
  });
});

describe('classifyEmbryosAtRoi (dominant, an ROI in PAR1)', () => {
  // In a pseudo-autosomal region a son carries his father's copy too.
  const parRegion = { chr: 'X', start: 600_000, end: 650_000 };
  const parSeg = (hap1: string, hap2: string, lineage: [string, string]) => ({
    ...seg(hap1, hap2, lineage[0], lineage[1], 10_001, 2_781_479),
    chr: 'X',
    hemizygous_in_males: false,
  });
  const members: HaplotypeMemberLike[] = [
    { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
    { sample_id: 'DAUGHTER', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'E_RISK', role: 'embryo', affected: false, sex: 'male' },
    { sample_id: 'E_CLEAR', role: 'embryo', affected: false, sex: 'male' },
  ];
  const samples: HaplotypeSampleLike[] = [
    { sample: 'FATHER', segments: [parSeg('1', '0', ['paternal', 'paternal'])] },
    { sample: 'DAUGHTER', segments: [parSeg('1', '0', ['paternal', 'maternal'])] },
    { sample: 'E_RISK', segments: [parSeg('1', '0', ['paternal', 'maternal'])] },
    { sample: 'E_CLEAR', segments: [parSeg('0', '1', ['paternal', 'maternal'])] },
  ];

  it("calls a son on both copies: his father's risk haplotype is at risk", () => {
    const out = byId(classifyEmbryosAtRoi({ members, samples, inheritanceModel: 'AD', region: parRegion }));
    expect(out.E_RISK.state).toBe('affected_or_at_risk');
    expect(out.E_CLEAR.state).toBe('unaffected_non_carrier');
  });
});

describe('hasRecombinationNearRoi', () => {
  it('flags a block boundary inside the ROI', () => {
    const segs = [
      seg('1', '1', 'paternal', 'maternal', 0, 1_000_500),
      seg('0', '1', 'paternal', 'maternal', 1_000_500, 2_000_000), // crossover at 1,000,500 (inside ROI)
    ];
    expect(hasRecombinationNearRoi(segs, region)).toBe(true);
  });

  it('does not flag a boundary far from the ROI', () => {
    const segs = [
      seg('1', '1', 'paternal', 'maternal', 0, 500_000),
      seg('0', '1', 'paternal', 'maternal', 500_000, 2_000_000), // crossover at 500k, >250k from ROI
    ];
    expect(hasRecombinationNearRoi(segs, region)).toBe(false);
  });
});
