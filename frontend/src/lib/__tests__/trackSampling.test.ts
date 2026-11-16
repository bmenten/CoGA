import {
  getAdaptiveTrackWindow,
  getApcadPointLimit,
  getTrackBinLimit,
  getTrackSegmentLimit,
  getTrackVariantLimit,
  PHASED_MARKER_MAX_SPAN,
  SMALL_VARIANT_ALL_MARKS_MAX_SPAN,
  SMALL_VARIANT_TRACK_RESULT_LIMIT,
  formatSpanLimit,
  shouldShowPhasedMarkers,
  smallVariantTrackScope,
} from '../trackSampling';

describe('trackSampling', () => {
  it('bounds variant limits to a viewport-scaled range', () => {
    expect(getTrackVariantLimit(100)).toBe(400);
    expect(getTrackVariantLimit(600)).toBe(1200);
    expect(getTrackVariantLimit(4000)).toBe(4000);
  });

  it('bounds bed bin and segment limits', () => {
    expect(getTrackBinLimit(100)).toBe(300);
    expect(getTrackBinLimit(700)).toBe(1400);
    expect(getTrackSegmentLimit(100)).toBe(200);
    expect(getTrackSegmentLimit(700)).toBe(1050);
  });

  it('gives APCAD a larger, bounded point budget than the binned tracks', () => {
    // Denser than getTrackBinLimit so the BAF bands read as bands, not a dotted line.
    expect(getApcadPointLimit(1000)).toBe(5000);
    expect(getApcadPointLimit(1000)).toBeGreaterThan(getTrackBinLimit(1000));
    expect(getApcadPointLimit(100)).toBe(2000); // floor
    expect(getApcadPointLimit(4000)).toBe(8000); // ceiling
    expect(getApcadPointLimit(0)).toBe(4000); // fallback for unmeasured width
  });

  it('expands the aggregation window for wide genomic spans', () => {
    expect(getAdaptiveTrackWindow(250_000_000, 1200, 10_000)).toBeGreaterThan(10_000);
    expect(getAdaptiveTrackWindow(100_000, 1200, 10_000)).toBe(10_000);
    expect(getAdaptiveTrackWindow(0, 1200, 10_000)).toBe(10_000);
  });

  it('draws every small variant up to 250 kb, the salient ones alone beyond', () => {
    expect(SMALL_VARIANT_ALL_MARKS_MAX_SPAN).toBe(250_000);
    expect(smallVariantTrackScope(1)).toBe('all');
    expect(smallVariantTrackScope(250_000)).toBe('all');
    expect(smallVariantTrackScope(250_001)).toBe('salient');
    expect(smallVariantTrackScope(2_000_000)).toBe('salient');
    expect(smallVariantTrackScope(250_000_000)).toBe('salient');
    expect(smallVariantTrackScope(0)).toBe('none');
    expect(smallVariantTrackScope(Number.NaN)).toBe('none');
  });

  it('draws the phased markers in a window of 5 Mb or less', () => {
    expect(PHASED_MARKER_MAX_SPAN).toBe(5_000_000);
    expect(shouldShowPhasedMarkers(2_400_000)).toBe(true);
    expect(shouldShowPhasedMarkers(5_000_000)).toBe(true);
    expect(shouldShowPhasedMarkers(5_000_001)).toBe(false);
    expect(shouldShowPhasedMarkers(248_956_422)).toBe(false);
    expect(shouldShowPhasedMarkers(0)).toBe(false);
  });

  it('writes a window limit as the track heads do', () => {
    expect(formatSpanLimit(250_000)).toBe('250 kb');
    expect(formatSpanLimit(5_000_000)).toBe('5 Mb');
  });

  it('uses a strict small-variant track display threshold', () => {
    expect(SMALL_VARIANT_TRACK_RESULT_LIMIT).toBe(10000);
  });
});
