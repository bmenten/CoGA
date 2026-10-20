import { describe, expect, it } from 'vitest';

import {
  apcadAxisMax,
  coverageScale,
  coverageScaleNote,
  coverageSourceLabel,
  coverageTrackLabel,
  orderCoverageSources,
} from '../coverageSources';

describe('coverage sources', () => {
  it('orders callers the same way for every sample', () => {
    // A trio with three callers is nine coverage tracks. Reading them down a
    // column only works if the same caller occupies the same row each time, so
    // the order cannot depend on what the availability query happened to return.
    expect(orderCoverageSources(['qdnaseq', 'hificnv', 'wisecondorx'])).toEqual([
      'hificnv',
      'wisecondorx',
      'qdnaseq',
    ]);
    expect(orderCoverageSources(['wisecondorx', 'qdnaseq'])).toEqual(['wisecondorx', 'qdnaseq']);
  });

  it('keeps an unrecognised caller, sorted after the known ones', () => {
    // Dropping a source would silently hide a track that has data.
    expect(orderCoverageSources(['spectre', 'hificnv', 'cnvkit'])).toEqual([
      'hificnv',
      'cnvkit',
      'spectre',
    ]);
  });

  it('de-duplicates and tolerates nothing at all', () => {
    expect(orderCoverageSources(['hificnv', 'hificnv'])).toEqual(['hificnv']);
    expect(orderCoverageSources([])).toEqual([]);
    expect(orderCoverageSources(undefined)).toEqual([]);
  });

  it('labels known callers the way the tools spell themselves', () => {
    expect(coverageSourceLabel('hificnv')).toBe('HiFiCNV');
    expect(coverageSourceLabel('wisecondorx')).toBe('WisecondorX');
    expect(coverageSourceLabel('qdnaseq')).toBe('QDNAseq');
  });

  it('shows an unknown caller verbatim rather than title-casing it', () => {
    // Title-casing produced "Hificnv"/"Glnexus" elsewhere in the app; the tag the
    // pipeline wrote is more useful than a guessed capitalisation.
    expect(coverageSourceLabel('cnvkit')).toBe('cnvkit');
  });

  it('only names the caller when there is more than one to tell apart', () => {
    expect(coverageTrackLabel('hificnv', 1)).toBe('Coverage');
    expect(coverageTrackLabel('hificnv', 3)).toBe('Coverage · HiFiCNV');
  });

  // TRACE-9: each coverage track is drawn on the axis of what it holds, as the track availability
  // serves it (`coverage_contents`, decided by the rule the depth readers read it by).
  it('draws each track on the axis of what it holds', () => {
    expect(coverageScale('log2_ratio')).toBe('log2_ratio');
    expect(coverageScale('read_depth')).toBe('read_depth');
    // A coverage BED that does not declare what it holds, a transform not known, or a track the
    // availability says nothing of: on neither axis.
    expect(coverageScale('unknown')).toBe('unknown');
    expect(coverageScale('counts')).toBe('unknown');
    expect(coverageScale(undefined)).toBe('unknown');
  });

  it('says nothing where the legend says it all', () => {
    // A log2 ratio: the WisecondorX and QDNAseq bins, HiFiCNV's normalised depth, a BED declared
    // as one.
    expect(coverageScaleNote('wisecondorx', 'log2_ratio')).toBeNull();
    expect(coverageScaleNote('web', 'log2_ratio')).toBeNull();
    // A coverage BED declared as read depth: the depth axis says so.
    expect(coverageScaleNote('web', 'read_depth')).toBeNull();
  });

  it('says why depths stored as read are on a depth axis, beside the callers log2 ratios', () => {
    // TRACE-7: a HiFiCNV depth file without a usable autosomal median, stored as read. It used to
    // be left off the chart, the log2-ratio axis being the only one.
    expect(coverageScaleNote('hificnv', 'read_depth', ['none'])).toEqual({
      text: 'Read depth, stored as read: the sample had no usable autosomal median to normalise it by.',
      warning: false,
    });
  });

  it('warns that values of unknown kind are neither depth nor a log2 ratio', () => {
    // A coverage BED that does not declare what it holds: it was drawn on the log2-ratio axis
    // while the depth readers read it as depth.
    expect(coverageScaleNote('web', 'unknown')).toEqual({
      text: 'This coverage BED does not declare whether it holds read depth or a log2 ratio: drawn on its own range without an axis, and not read as depth. Load it again with what it holds.',
      warning: true,
    });
    expect(coverageScaleNote('web', undefined)?.warning).toBe(true);
    // Another caller whose record names a transform not known here.
    expect(coverageScaleNote('hificnv', 'unknown', ['log10_ratio'])).toEqual({
      text: 'What these values are is not recorded: drawn on their own range without an axis, and not read as depth.',
      warning: true,
    });
  });

  it('folds the APCAD axis only when every caller on it is folded', () => {
    // A depth caller writes min(AF, 1-AF), so its points never exceed 0.5; on the
    // 0-1 axis the phased track uses, the whole upper half is dead space.
    expect(apcadAxisMax(['hificnv'])).toBe(0.5);
    // Parent-of-origin markers run the full B-allele range.
    expect(apcadAxisMax(['apcad'])).toBe(1);
    // Mixed: the phased track needs the full range, so the folded one shares it
    // rather than having its markers clipped away.
    expect(apcadAxisMax(['apcad', 'hificnv'])).toBe(1);
    // Nothing to draw: keep the conventional axis.
    expect(apcadAxisMax([])).toBe(1);
    expect(apcadAxisMax(undefined)).toBe(1);
  });
});
