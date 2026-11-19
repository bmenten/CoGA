// How the views draw a conventional NIPT's maternal plasma (ccnv): the expected log2 ratio of a
// fetal trisomy and monosomy at the plasma's fetal fraction, where the lines are drawn, the
// track head's summary and a segment's tooltip. All values are synthetic.
import { describe, expect, it } from 'vitest';
import type { CcnvTrackSummaryOut } from '../apiSchema.generated';
import {
  ccnvFetalFractionEstimates,
  ccnvSegmentCall,
  ccnvSegmentDiscordant,
  ccnvTrackHeadline,
  drawsFetalFractionLines,
  fetalMonosomyLog2,
  fetalTrisomyLog2,
  segmentDetailLines,
} from '../ccnvTracks';

const summary: CcnvTrackSummaryOut = {
  fetal_fraction: 0.1,
  active_ff: 0.085,
  active_ff_se: 0.005,
  active_ff_source: 'FUSED',
  ffy: 0.084,
  ffx: null,
  ffa: 0.09,
  ff_size: 0.06,
  fetal_sex: 'M',
  sca_call: 'XY',
  sca_confidence: 0.9991,
  low_ff_warning: false,
};

describe('ccnvTracks', () => {
  it('places a fetal trisomy and monosomy at log2(1 ± FF/2), as ccnv’s expected ratios', () => {
    // At 10 %: three copies in a tenth of the fetal half, log2(1.05) and log2(0.95).
    expect(fetalTrisomyLog2(0.1)).toBeCloseTo(0.070389, 6);
    expect(fetalMonosomyLog2(0.1)).toBeCloseTo(-0.074001, 6);
    expect(fetalTrisomyLog2(0)).toBe(0);
  });

  it('draws the lines on chromosomes 1–22 and X, never on Y', () => {
    expect(['1', 'chr21', 'X', 'chrX'].map(drawsFetalFractionLines)).toEqual([true, true, true, true]);
    expect(['Y', 'chrY'].map(drawsFetalFractionLines)).toEqual([false, false]);
  });

  it('heads the track with the fetal fraction the lines are drawn at, the fetal sex and the SCA call', () => {
    expect(ccnvTrackHeadline(summary)).toBe('FF 10.0% · fetal sex M · SCA XY');
    expect(ccnvFetalFractionEstimates(summary)).toEqual([
      'Effective FF 10.0%: the expected lines are drawn at it',
      'Active FF 8.5% ± 0.5% (FUSED)',
      'FFY (chrY) 8.4%',
      'FFA (autosomes) 9.0%',
      'FF from fragment sizes 6.0%',
      'SCA XY, confidence 99.91%',
    ]);
    expect(ccnvFetalFractionEstimates({ ...summary, low_ff_warning: true }).at(-1)).toBe(
      'ccnv warns of a low fetal fraction',
    );
  });

  it('reads a segment’s call and discordance from its details, and lists them in a fixed order', () => {
    const details = {
      origin_call: 'MATERNAL_MOSAIC',
      discordance_reason: 'SUB_FETAL_RATIO',
      discordant: true,
      implied_ff: 0.0135,
      expected_ratio: 0.08482,
      call: 'gain',
      zscore: 4.21357,
      bin_count: 480,
      weight: 81234.5,
    };
    expect(ccnvSegmentCall(details)).toBe('gain');
    expect(ccnvSegmentCall({ zscore: 0.2 })).toBeNull();
    expect(ccnvSegmentDiscordant(details)).toBe(true);
    expect(ccnvSegmentDiscordant(null)).toBe(false);
    // The weight is ccnv's precision weight, for the segmentation: not listed.
    expect(segmentDetailLines(details)).toEqual([
      'Call: gain',
      'z-score: 4.21',
      'Bins: 480',
      'Expected log2 ratio: 0.0848',
      'Implied fetal fraction: 1.4%',
      'Discordant: yes',
      'Discordance: SUB_FETAL_RATIO',
      'Origin: MATERNAL_MOSAIC',
    ]);
    // A WisecondorX segment's z-score alone.
    expect(segmentDetailLines({ zscore: -3.1 })).toEqual(['z-score: -3.1']);
    expect(segmentDetailLines(null)).toEqual([]);
  });
});
