import {
  getAdaptiveTrackWindow,
  getTrackBinLimit,
  getTrackSegmentLimit,
  getTrackVariantLimit,
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

  it('expands the aggregation window for wide genomic spans', () => {
    expect(getAdaptiveTrackWindow(250_000_000, 1200, 10_000)).toBeGreaterThan(10_000);
    expect(getAdaptiveTrackWindow(100_000, 1200, 10_000)).toBe(10_000);
    expect(getAdaptiveTrackWindow(0, 1200, 10_000)).toBe(10_000);
  });
});
