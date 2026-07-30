import { describe, expect, it } from 'vitest';

import {
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
});
