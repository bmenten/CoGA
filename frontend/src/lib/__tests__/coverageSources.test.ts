import { describe, expect, it } from 'vitest';

import {
  apcadAxisMax,
  coverageNotDrawnNote,
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

  // TRACE-7: the chart's axis is a log2 ratio, and a track is drawn on it only when its
  // record says its values are one.
  it('draws a track whose values are a log2 ratio on the log2-ratio axis', () => {
    // Put on that scale by the import: HiFiCNV's depth and copy number.
    expect(coverageNotDrawnNote(['log2_ratio_to_autosomal_median'])).toBeNull();
    expect(coverageNotDrawnNote(['log2_ratio_to_diploid'])).toBeNull();
    // No transform recorded: the file's own values, which WisecondorX and QDNAseq write as
    // log2 ratios.
    expect(coverageNotDrawnNote([])).toBeNull();
    expect(coverageNotDrawnNote(undefined)).toBeNull();
  });

  it('leaves depths stored as read off the log2-ratio axis, and says why', () => {
    // A HiFiCNV depth file without a usable autosomal median: raw depth, which drawn on
    // the axis sat above its top and read as a gain across the genome.
    const note =
      'Not drawn: no usable autosomal median, so these depths are stored as read, not as a log2 ratio. IGV shows the depth file.';
    expect(coverageNotDrawnNote(['none'])).toBe(note);
    // Two records that disagree are not averaged into a scale: one of them is not a ratio.
    expect(coverageNotDrawnNote(['log2_ratio_to_autosomal_median', 'none'])).toBe(note);
  });

  it('leaves a transform it does not know off the log2-ratio axis', () => {
    expect(coverageNotDrawnNote(['log10_ratio'])).toBe(
      'Not drawn: these values are stored as log10_ratio, not as a log2 ratio.',
    );
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
