import { describe, expect, it } from 'vitest';
import {
  assessSampleHaplotypeRisk,
  diseaseHaplotypeKindForLane,
  getHaplotypeLaneSignature,
  getRenderableHaplotypeLanes,
  inferDiseaseHaplotypes,
  interpretSampleHaplotypeRisk,
  type HaplotypeMemberLike,
  type HaplotypeSampleLike,
  type HaplotypeSegmentLike,
} from '../haplotypeRisk';

const region = { chr: '1', start: 40, end: 60 };

describe('haplotype risk inference', () => {
  it('identifies the dominant haplotype shared by an affected parent and child', () => {
    const members: HaplotypeMemberLike[] = [
      { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'EMBRYO1', role: 'embryo', affected: false, sex: 'female' },
    ];
    const samples: HaplotypeSampleLike[] = [
      {
        sample: 'FATHER',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' }],
      },
      {
        sample: 'PROBAND',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '0' }],
      },
      {
        sample: 'EMBRYO1',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '1' }],
      },
    ];

    const model = inferDiseaseHaplotypes({
      samples,
      members,
      inheritanceModel: 'AD',
      region,
    });

    expect(model.signatures).toEqual([{ origin: 'paternal', value: '1', kind: 'dominant' }]);
    expect(diseaseHaplotypeKindForLane(model, members[2], samples[2].segments[0], 'hap1', '1')).toBe('dominant');
    expect(diseaseHaplotypeKindForLane(model, members[2], samples[2].segments[0], 'hap2', '1')).toBeNull();
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[2],
        region,
      }),
    ).toBe('affected_or_at_risk');
  });

  it('leaves dominant coloring neutral when a single affected sample is ambiguous', () => {
    const members: HaplotypeMemberLike[] = [{ sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' }];
    const samples: HaplotypeSampleLike[] = [
      {
        sample: 'PROBAND',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '1' }],
      },
    ];

    const model = inferDiseaseHaplotypes({
      samples,
      members,
      inheritanceModel: 'AD',
      region,
    });

    expect(model.informative).toBe(false);
    expect(model.signatures).toEqual([]);
  });

  it('infers paternal and maternal recessive risk haplotypes from an affected child', () => {
    const members: HaplotypeMemberLike[] = [
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      {
        sample_id: 'EMBRYO_RISK',
        role: 'embryo',
        affected: false,
        sex: 'female',
      },
      {
        sample_id: 'EMBRYO_CARRIER',
        role: 'embryo',
        affected: false,
        sex: 'female',
      },
      {
        sample_id: 'EMBRYO_CLEAR',
        role: 'embryo',
        affected: false,
        sex: 'female',
      },
    ];
    const samples: HaplotypeSampleLike[] = [
      {
        sample: 'PROBAND',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '0' }],
      },
      {
        sample: 'EMBRYO_RISK',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '0' }],
      },
      {
        sample: 'EMBRYO_CARRIER',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '1' }],
      },
      {
        sample: 'EMBRYO_CLEAR',
        segments: [{ chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' }],
      },
    ];

    const model = inferDiseaseHaplotypes({
      samples,
      members,
      inheritanceModel: 'AR',
      region,
    });

    expect(model.signatures).toEqual([
      { origin: 'paternal', value: '1', kind: 'recessive-paternal' },
      { origin: 'maternal', value: '0', kind: 'recessive-maternal' },
    ]);
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[1],
        region,
      }),
    ).toBe('affected_or_at_risk');
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[2],
        region,
      }),
    ).toBe('carrier');
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[3],
        region,
      }),
    ).toBe('unaffected_non_carrier');
  });

  it('returns uninformative for a recessive embryo when only one side resolves (donor/unknown side)', () => {
    // Single-parent / donor PGT: only the known parent's (paternal) risk haplotype
    // can be resolved; the donor (maternal) side is unknown. A recessive call needs
    // BOTH sides, so the embryo must fall back to uninformative — never a confident
    // "at risk"/"clear" — even though the model carries a paternal signature.
    const members: HaplotypeMemberLike[] = [
      { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
      { sample_id: 'EMBRYO', role: 'embryo', affected: false, sex: 'female' },
    ];
    const samples: HaplotypeSampleLike[] = [
      // maternal lane non-informative ('.') -> only the paternal side resolves
      { sample: 'PROBAND', segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '.' }] },
      { sample: 'EMBRYO', segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '0' }] },
    ];

    const model = inferDiseaseHaplotypes({ samples, members, inheritanceModel: 'AR', region });

    expect(model.signatures.map((s) => s.kind)).toEqual(['recessive-paternal']);
    expect(
      interpretSampleHaplotypeRisk({ model, samples, member: members[1], region }),
    ).toBe('uninformative');
  });

  it('handles X-linked recessive male hemizygosity and carrier females', () => {
    const xRegion = { chr: 'X', start: 40, end: 60 };
    const members: HaplotypeMemberLike[] = [
      {
        sample_id: 'AFFECTED_SON',
        role: 'proband',
        affected: true,
        sex: 'male',
      },
      {
        sample_id: 'CARRIER_DAUGHTER',
        role: 'sibling',
        affected: false,
        sex: 'female',
        carrier_status: 'carrier',
      },
      { sample_id: 'CLEAR_SON', role: 'sibling', affected: false, sex: 'male' },
    ];
    const samples: HaplotypeSampleLike[] = [
      {
        sample: 'AFFECTED_SON',
        segments: [{ chr: 'X', start: 0, end: 100, hap1: '0', hap2: '1' }],
      },
      {
        sample: 'CARRIER_DAUGHTER',
        segments: [{ chr: 'X', start: 0, end: 100, hap1: '0', hap2: '1' }],
      },
      {
        sample: 'CLEAR_SON',
        segments: [{ chr: 'X', start: 0, end: 100, hap1: '0', hap2: '0' }],
      },
    ];

    const model = inferDiseaseHaplotypes({
      samples,
      members,
      inheritanceModel: 'XLR',
      region: xRegion,
    });

    expect(model.signatures).toEqual([{ origin: 'maternal', value: '1', kind: 'x-linked' }]);
    expect(getRenderableHaplotypeLanes(members[0], samples[0].segments[0], 'X')).toEqual(['hap2']);
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[0],
        region: xRegion,
      }),
    ).toBe('affected_or_at_risk');
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[1],
        region: xRegion,
      }),
    ).toBe('carrier');
    expect(
      interpretSampleHaplotypeRisk({
        model,
        samples,
        member: members[2],
        region: xRegion,
      }),
    ).toBe('unaffected_non_carrier');
  });
});

describe('pedigree-aware lineage tags override role-based origin', () => {
  // co620: the paternal grandmother is stored with role "mother" but the backend
  // tags her shared homolog paternal and her other homolog untransmitted.
  const grandmother: HaplotypeMemberLike = { sample_id: 'GM', role: 'mother', affected: true, sex: 'female' };

  it('uses the lineage tag, not the role, to pick the origin', () => {
    const seg = {
      chr: '1',
      start: 0,
      end: 100,
      hap1: '0',
      hap2: '1',
      hap1_lineage: 'untransmitted',
      hap2_lineage: 'paternal',
    };
    // hap2 is the shared paternal homolog (light blue, shade 1) despite role "mother".
    expect(getHaplotypeLaneSignature(grandmother, seg, 'hap2', '1')).toEqual({ origin: 'paternal', value: '1' });
    // hap1 is untransmitted -> no signature -> rendered grey.
    expect(getHaplotypeLaneSignature(grandmother, seg, 'hap1', '1')).toBeNull();
  });

  it('falls back to role-based origin when no lineage tag is present', () => {
    const seg = { chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' };
    // role "mother" -> both lanes maternal (the legacy behaviour, still used for
    // the nuclear core and for un-tagged data).
    expect(getHaplotypeLaneSignature(grandmother, seg, 'hap1', '1')).toEqual({ origin: 'maternal', value: '0' });
    expect(getHaplotypeLaneSignature(grandmother, seg, 'hap2', '1')).toEqual({ origin: 'maternal', value: '1' });
  });

  it('infers the dominant disease haplotype from an affected father + lineage-tagged grandmother', () => {
    const members: HaplotypeMemberLike[] = [
      { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
      grandmother,
    ];
    const samples: HaplotypeSampleLike[] = [
      { sample: 'FATHER', segments: [{ chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' }] },
      {
        sample: 'GM',
        segments: [
          {
            chr: '1',
            start: 0,
            end: 100,
            hap1: '0',
            hap2: '1',
            hap1_lineage: 'untransmitted',
            hap2_lineage: 'paternal',
          },
        ],
      },
    ];
    const model = inferDiseaseHaplotypes({ samples, members, inheritanceModel: 'AD', region });
    // The shared paternal homolog (shade 1, light blue) carries the dominant allele.
    expect(model.signatures).toEqual([{ origin: 'paternal', value: '1', kind: 'dominant' }]);
  });

  it('does not let a fully-greyed affected relative collapse a resolvable dominant call', () => {
    // Regression for the intersection-collapse bug: the affected father + affected
    // proband co-segregate paternal:0, so the dominant call must resolve. An affected
    // relative the lineage service greyed on BOTH lanes (e.g. the co620 grandmother on
    // an autosome where IBD matching failed the gates, or any non-autosome) yields ZERO
    // in-region signatures. She must be treated as NON-informative for the intersection,
    // not as a hard zero that wipes the whole disease-haplotype call.
    const father: HaplotypeMemberLike = { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' };
    const proband: HaplotypeMemberLike = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' };
    const greyedRelative: HaplotypeMemberLike = { sample_id: 'GM', role: 'mother', affected: true, sex: 'female' };
    const members = [father, proband, greyedRelative];
    const baseSamples: HaplotypeSampleLike[] = [
      { sample: 'FATHER', segments: [{ chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' }] },
      { sample: 'PROBAND', segments: [{ chr: '1', start: 0, end: 100, hap1: '0', hap2: '1' }] },
    ];

    // Father + proband alone resolve paternal:0.
    const baseModel = inferDiseaseHaplotypes({
      samples: baseSamples,
      members: [father, proband],
      inheritanceModel: 'AD',
      region,
    });
    expect(baseModel.signatures).toEqual([{ origin: 'paternal', value: '0', kind: 'dominant' }]);
    expect(baseModel.informative).toBe(true);

    // Adding the greyed affected relative (both lanes untransmitted/unknown) must NOT
    // wipe the call.
    const withGreyed = inferDiseaseHaplotypes({
      samples: [
        ...baseSamples,
        {
          sample: 'GM',
          segments: [
            {
              chr: '1',
              start: 0,
              end: 100,
              hap1: '0',
              hap2: '1',
              hap1_lineage: 'unknown',
              hap2_lineage: 'untransmitted',
            },
          ],
        },
      ],
      members,
      inheritanceModel: 'AD',
      region,
    });
    expect(withGreyed.informative).toBe(true);
    expect(withGreyed.signatures).toEqual([{ origin: 'paternal', value: '0', kind: 'dominant' }]);
  });

  it('still resolves a recessive side when one affected member is greyed on that side', () => {
    // The per-origin intersection has the same collapse hazard. An affected proband
    // resolves recessive paternal:1 / maternal:0; an affected relative greyed on both
    // lanes must not zero the per-origin intersection.
    const proband: HaplotypeMemberLike = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' };
    const greyedRelative: HaplotypeMemberLike = { sample_id: 'AUNT', role: 'mother', affected: true, sex: 'female' };
    const model = inferDiseaseHaplotypes({
      samples: [
        { sample: 'PROBAND', segments: [{ chr: '1', start: 0, end: 100, hap1: '1', hap2: '0' }] },
        {
          sample: 'AUNT',
          segments: [
            {
              chr: '1',
              start: 0,
              end: 100,
              hap1: '0',
              hap2: '1',
              hap1_lineage: 'untransmitted',
              hap2_lineage: 'unknown',
            },
          ],
        },
      ],
      members: [proband, greyedRelative],
      inheritanceModel: 'AR',
      region,
    });
    expect(model.signatures).toEqual([
      { origin: 'paternal', value: '1', kind: 'recessive-paternal' },
      { origin: 'maternal', value: '0', kind: 'recessive-maternal' },
    ]);
  });
});

// No data is not a clear side. A negative call (Unaffected, or the clear side of a Carrier
// call) must rest on the member's OWN haplotype at the ROI, on the parental side that call
// needs. It used to rest on "no risk haplotype found", which is also what a member with no
// block over the ROI finds, so an embryo without data there was called Unaffected.
describe('a negative call needs the member\'s own haplotype across the ROI', () => {
  type Seg = HaplotypeSegmentLike;
  const block = (start: number, end: number, hap1: string, hap2: string, extra: Partial<Seg> = {}): Seg => ({
    chr: '1',
    start,
    end,
    hap1,
    hap2,
    ...extra,
  });
  const riskOf = (
    samples: HaplotypeSampleLike[],
    members: HaplotypeMemberLike[],
    inheritanceModel: string,
    member: HaplotypeMemberLike,
    roi: { chr: string; start: number; end: number } = region,
  ) =>
    interpretSampleHaplotypeRisk({
      model: inferDiseaseHaplotypes({ samples, members, inheritanceModel, region: roi }),
      samples,
      member,
      region: roi,
    });

  describe('dominant: the affected father and proband share paternal 1 at the ROI', () => {
    const father: HaplotypeMemberLike = { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' };
    const proband: HaplotypeMemberLike = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' };
    const embryo: HaplotypeMemberLike = { sample_id: 'EMBRYO', role: 'embryo', affected: false, sex: 'female' };
    const family: HaplotypeSampleLike[] = [
      { sample: 'FATHER', segments: [block(0, 100, '0', '1')] },
      { sample: 'PROBAND', segments: [block(0, 100, '1', '0')] },
    ];
    const members = [father, proband, embryo];
    const withEmbryo = (segments: Seg[] | null) =>
      segments === null ? family : [...family, { sample: 'EMBRYO', segments }];
    const embryoRisk = (segments: Seg[] | null) => riskOf(withEmbryo(segments), members, 'AD', embryo);

    it('resolves the disease haplotype the embryo calls below are made against', () => {
      const model = inferDiseaseHaplotypes({ samples: family, members, inheritanceModel: 'AD', region });
      expect(model.signatures).toEqual([{ origin: 'paternal', value: '1', kind: 'dominant' }]);
    });

    it.each([
      ['has blocks on the chromosome but none over the ROI', [block(0, 30, '0', '1'), block(70, 100, '0', '1')]],
      ['has no haplotype blocks at all', null],
      ['has no blocks in the response (empty list)', []],
      ['has a block over only part of the ROI', [block(0, 50, '0', '1')]],
      [
        'is grey at the ROI (lineage unknown on both lanes)',
        [block(0, 100, '0', '1', { hap1_lineage: 'unknown', hap2_lineage: 'unknown' })],
      ],
      [
        'is grey on the risk side (paternal lane untransmitted)',
        [block(0, 100, '0', '1', { hap1_lineage: 'untransmitted', hap2_lineage: 'maternal' })],
      ],
      ['has its paternal homolog unconfirmed (?) at the ROI', [block(0, 100, '?', '1')]],
      ['has its paternal homolog deleted (.) at the ROI', [block(0, 100, '.', '1')]],
      [
        'carries a paternal homolog only up to a gap inside the ROI',
        [block(0, 45, '0', '1'), block(55, 100, '0', '1')],
      ],
    ])('is uninformative, not unaffected, when the embryo %s', (_label, segments) => {
      expect(embryoRisk(segments)).toBe('uninformative');
    });

    it('still calls an embryo at risk when it carries the risk haplotype over part of the ROI', () => {
      // A positive call stays conservative: the risk haplotype seen anywhere at the ROI.
      expect(embryoRisk([block(0, 50, '1', '0')])).toBe('affected_or_at_risk');
    });

    it('still calls an embryo unaffected when adjacent blocks cover the ROI between them', () => {
      expect(embryoRisk([block(0, 50, '0', '1'), block(50, 100, '0', '0')])).toBe('unaffected_non_carrier');
      // Blocks written as closed intervals (end + 1 = next start) leave no position uncovered.
      expect(embryoRisk([block(0, 49, '0', '1'), block(50, 100, '0', '0')])).toBe('unaffected_non_carrier');
    });

    it('does not need the other parent\'s side: a donor family\'s embryo is called on the known side', () => {
      // Single-parent (donor) PGT: the donor lane is grey by construction. The dominant
      // haplotype is paternal, so the paternal lane alone decides.
      expect(
        embryoRisk([block(0, 100, '0', '0', { hap1_lineage: 'paternal', hap2_lineage: 'untransmitted' })]),
      ).toBe('unaffected_non_carrier');
    });

    it('does not take an unconfirmed side (?) the affected share for the disease haplotype', () => {
      // Before the first informative site of a side the trio block builder writes '?'. Two
      // affected children with '?' there share no known homolog; reading '?' as one made it
      // the "disease haplotype", and every embryo with a confirmed paternal homolog read
      // unaffected against it.
      const sib: HaplotypeMemberLike = { sample_id: 'SIB', role: 'sibling', affected: true, sex: 'male' };
      const samples: HaplotypeSampleLike[] = [
        { sample: 'PROBAND', segments: [block(0, 100, '?', '0')] },
        { sample: 'SIB', segments: [block(0, 100, '?', '1')] },
        { sample: 'EMBRYO', segments: [block(0, 100, '0', '1')] },
      ];
      const affectedChildren = [proband, sib, embryo];
      const model = inferDiseaseHaplotypes({ samples, members: affectedChildren, inheritanceModel: 'AD', region });
      expect(model.informative).toBe(false);
      expect(interpretSampleHaplotypeRisk({ model, samples, member: embryo, region })).toBe('uninformative');
    });

    it('reads a single-position ROI covered by the block around it', () => {
      const point = { chr: '1', start: 50, end: 50 };
      const samples = withEmbryo([block(0, 100, '0', '1')]);
      expect(riskOf(samples, members, 'AD', embryo, point)).toBe('unaffected_non_carrier');
      expect(riskOf(withEmbryo([block(60, 100, '0', '1')]), members, 'AD', embryo, point)).toBe('uninformative');
    });
  });

  describe('recessive: the affected proband resolves paternal 1 and maternal 0', () => {
    const proband: HaplotypeMemberLike = { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' };
    const embryo: HaplotypeMemberLike = { sample_id: 'EMBRYO', role: 'embryo', affected: false, sex: 'female' };
    const embryoRisk = (segments: Seg[] | null) =>
      riskOf(
        [
          { sample: 'PROBAND', segments: [block(0, 100, '1', '0')] },
          ...(segments === null ? [] : [{ sample: 'EMBRYO', segments }]),
        ],
        [proband, embryo],
        'AR',
        embryo,
      );

    it.each([
      ['has no haplotype blocks at all', null],
      ['has blocks only outside the ROI', [block(70, 100, '0', '1')]],
      ['is clear on the paternal side but has no maternal homolog at the ROI', [block(0, 100, '0', '.')]],
      [
        'is clear on the maternal side but grey on the paternal side',
        [block(0, 100, '0', '1', { hap1_lineage: 'unknown', hap2_lineage: 'maternal' })],
      ],
    ])('is uninformative, not unaffected, when the embryo %s', (_label, segments) => {
      expect(embryoRisk(segments)).toBe('uninformative');
    });

    it('is uninformative, not a carrier, when the other side is missing at the ROI', () => {
      // A carrier call says the other homolog is clear; with no maternal homolog seen the
      // embryo may as well be affected.
      expect(embryoRisk([block(0, 100, '1', '.')])).toBe('uninformative');
      // Maternal 1 (clear) seen over only part of the ROI: maternal 0 may be past it.
      expect(embryoRisk([block(0, 50, '1', '1')])).toBe('uninformative');
    });

    it('still makes the calls it has the data for', () => {
      expect(embryoRisk([block(0, 100, '1', '0')])).toBe('affected_or_at_risk');
      expect(embryoRisk([block(0, 50, '1', '0')])).toBe('affected_or_at_risk');
      expect(embryoRisk([block(0, 100, '1', '1')])).toBe('carrier');
      expect(embryoRisk([block(0, 100, '0', '1')])).toBe('unaffected_non_carrier');
    });
  });

  describe('X-linked: a son has one X, from his mother', () => {
    const xRoi = { chr: 'X', start: 40, end: 60 };
    const xBlock = (start: number, end: number, hap1: string, hap2: string): Seg =>
      block(start, end, hap1, hap2, { chr: 'X' });

    describe('recessive, resolved from an affected son (maternal 1)', () => {
      const affectedSon: HaplotypeMemberLike = { sample_id: 'SON', role: 'proband', affected: true, sex: 'male' };
      const risk = (member: HaplotypeMemberLike, segments: Seg[]) =>
        riskOf(
          [
            { sample: 'SON', segments: [xBlock(0, 100, '0', '1')] },
            { sample: member.sample_id, segments },
          ],
          [affectedSon, member],
          'XLR',
          member,
          xRoi,
        );
      const maleEmbryo: HaplotypeMemberLike = { sample_id: 'E_MALE', role: 'embryo', affected: false, sex: 'male' };
      const femaleEmbryo: HaplotypeMemberLike = {
        sample_id: 'E_FEMALE',
        role: 'embryo',
        affected: false,
        sex: 'female',
      };

      it('is uninformative for a male embryo with no X block over the ROI', () => {
        expect(risk(maleEmbryo, [xBlock(70, 100, '?', '0')])).toBe('uninformative');
        expect(risk(maleEmbryo, [xBlock(0, 100, '?', '?')])).toBe('uninformative');
      });

      it('is uninformative for a female embryo whose maternal homolog is missing at the ROI', () => {
        expect(risk(femaleEmbryo, [xBlock(0, 100, '0', '.')])).toBe('uninformative');
      });

      it('still calls a son from his maternal X alone, and a daughter carrier from hers', () => {
        // The trio block builder never confirms a son's paternal X side ('?'); his call rests
        // on the X he has.
        expect(risk(maleEmbryo, [xBlock(0, 100, '?', '0')])).toBe('unaffected_non_carrier');
        expect(risk(maleEmbryo, [xBlock(0, 100, '?', '1')])).toBe('affected_or_at_risk');
        expect(risk(femaleEmbryo, [xBlock(0, 100, '0', '1')])).toBe('carrier');
        expect(risk(femaleEmbryo, [xBlock(0, 100, '0', '0')])).toBe('unaffected_non_carrier');
      });
    });

    describe('dominant, from an affected father and daughter (paternal 1)', () => {
      const father: HaplotypeMemberLike = { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' };
      const daughter: HaplotypeMemberLike = { sample_id: 'DAUGHTER', role: 'proband', affected: true, sex: 'female' };
      const son: HaplotypeMemberLike = { sample_id: 'E_SON', role: 'embryo', affected: false, sex: 'male' };
      const risk = (segments: Seg[]) =>
        riskOf(
          [
            { sample: 'FATHER', segments: [xBlock(0, 100, '1', '1')] },
            { sample: 'DAUGHTER', segments: [xBlock(0, 100, '1', '0')] },
            { sample: 'E_SON', segments },
          ],
          [father, daughter, son],
          'XLD',
          son,
          xRoi,
        );

      it('calls a son unaffected on his maternal X, and uninformative without it', () => {
        expect(risk([xBlock(0, 100, '?', '0')])).toBe('unaffected_non_carrier');
        expect(risk([xBlock(70, 100, '?', '0')])).toBe('uninformative');
      });
    });

    // An embryo's sex is often not recorded. In X-linked recessive disease a son with the
    // maternal risk haplotype is affected and a daughter a carrier, so a call that depends on
    // the sex must not assume one. It used to assume a daughter and say "Carrier".
    describe("recessive, the embryo's sex not recorded", () => {
      const affectedSon: HaplotypeMemberLike = { sample_id: 'SON', role: 'proband', affected: true, sex: 'male' };
      const embryo: HaplotypeMemberLike = { sample_id: 'EMBRYO', role: 'embryo', affected: false, sex: 'unknown' };
      const assess = (members: HaplotypeMemberLike[], samples: HaplotypeSampleLike[]) =>
        assessSampleHaplotypeRisk({
          model: inferDiseaseHaplotypes({ samples, members, inheritanceModel: 'XLR', region: xRoi }),
          samples,
          member: embryo,
          region: xRoi,
        });
      // The affected son resolves the mother's risk X: maternal 1.
      const fromAffectedSon = (segments: Seg[]) =>
        assess(
          [affectedSon, embryo],
          [
            { sample: 'SON', segments: [xBlock(0, 100, '?', '1')] },
            { sample: 'EMBRYO', segments },
          ],
        );

      it('calls an embryo with the maternal risk haplotype at risk, not a carrier, and says why', () => {
        expect(fromAffectedSon([xBlock(0, 100, '?', '1')])).toEqual({
          state: 'affected_or_at_risk',
          roiNotCovered: false,
          sexDependent: { ifMale: 'affected_or_at_risk', ifFemale: 'carrier' },
        });
      });

      it('still calls an embryo with the other maternal X unaffected, whatever its sex', () => {
        const out = fromAffectedSon([xBlock(0, 100, '?', '0')]);
        expect(out.state).toBe('unaffected_non_carrier');
        expect(out.sexDependent ?? null).toBeNull();
      });

      it('is uninformative for missing data, not for its sex, when its maternal X is not seen', () => {
        const out = fromAffectedSon([xBlock(70, 100, '?', '1')]);
        expect(out.state).toBe('uninformative');
        expect(out.roiNotCovered).toBe(true);
        expect(out.sexDependent ?? null).toBeNull();
      });

      it('is uninformative, not a carrier or unaffected, when the calls differ and neither is at risk', () => {
        // An affected father's X: a daughter inherits it (a carrier), a son does not.
        const father: HaplotypeMemberLike = { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' };
        const fromAffectedFather = (segments: Seg[]) =>
          assess(
            [father, embryo],
            [
              { sample: 'FATHER', segments: [xBlock(0, 100, '1', '0')] },
              { sample: 'EMBRYO', segments },
            ],
          );
        expect(fromAffectedFather([xBlock(0, 100, '1', '0')])).toEqual({
          state: 'uninformative',
          roiNotCovered: false,
          sexDependent: { ifMale: 'unaffected_non_carrier', ifFemale: 'carrier' },
        });
        // No paternal X seen: a daughter's call has no data, a son's needs none.
        expect(fromAffectedFather([xBlock(0, 100, '?', '0')])).toEqual({
          state: 'uninformative',
          roiNotCovered: false,
          sexDependent: { ifMale: 'unaffected_non_carrier', ifFemale: 'uninformative' },
        });
      });

      it('keeps the call of an embryo whose sex is recorded', () => {
        const daughter = (segments: Seg[]) =>
          assessSampleHaplotypeRisk({
            model: inferDiseaseHaplotypes({
              samples: [{ sample: 'SON', segments: [xBlock(0, 100, '?', '1')] }],
              members: [affectedSon],
              inheritanceModel: 'XLR',
              region: xRoi,
            }),
            samples: [{ sample: 'EMBRYO', segments }],
            member: { ...embryo, sex: 'female' },
            region: xRoi,
          });
        const out = daughter([xBlock(0, 100, '?', '1')]);
        expect(out.state).toBe('carrier');
        expect(out.sexDependent ?? null).toBeNull();
      });
    });
  });
});

describe('degraded / empty input fails safe (REQ-PERF-003)', () => {
  it('yields a non-informative model and an uninformative embryo call', () => {
    const model = inferDiseaseHaplotypes({
      samples: [],
      members: [],
      inheritanceModel: 'AD',
      region,
    });
    expect(model.informative).toBe(false);
    expect(model.signatures).toEqual([]);

    const risk = interpretSampleHaplotypeRisk({
      model,
      samples: [],
      member: { sample_id: 'EMBRYO', role: 'embryo', sex: 'female' },
      region,
    });
    expect(risk).toBe('uninformative');
  });
});
