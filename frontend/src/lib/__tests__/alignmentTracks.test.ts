import { describe, expect, it } from 'vitest';

import type { AlignmentManifestEntryOut } from '../apiSchema.generated';
import { alignmentTrackName } from '../alignmentTracks';

const entry = (sample_id: string, analysis_type: string | null): AlignmentManifestEntryOut => ({
  sample_id,
  analysis_type,
  format: 'cram',
  url: `/cram/F1/${sample_id}.cram`,
  index_url: `/cram/F1/${sample_id}.cram.crai`,
});

// A sample sequenced short-read and then long-read has an alignment of each, each its own track.
describe('the name of an alignment track', () => {
  it('names the analysis of a sample with an alignment of more than one', () => {
    const entries = [entry('S1', 'long_read_wgs'), entry('S1', 'short_read_wgs'), entry('S2', 'long_read_wgs')];
    expect(entries.map((item) => alignmentTrackName(item, entries))).toEqual([
      'S1 (long-read WGS)',
      'S1 (short-read WGS)',
      'S2',
    ]);
  });

  it('names the analysis of every track of a family that holds several analyses', () => {
    // The short-read alignment of S1 could not be shown: its one track still says whose reads it holds.
    const entries = [entry('S1', 'long_read_wgs'), entry('S2', 'long_read_wgs'), entry('S3', null)];
    expect(entries.map((item) => alignmentTrackName(item, entries, true))).toEqual([
      'S1 (long-read WGS)',
      'S2 (long-read WGS)',
      // Found in the family's folders, recorded by no import.
      'S3 (analysis not recorded)',
    ]);
  });

  it('names the sample alone otherwise', () => {
    expect(alignmentTrackName(entry('S1', null), [entry('S1', null)])).toBe('S1');
    expect(alignmentTrackName(entry('S1', 'long_read_wgs'), [entry('S1', 'long_read_wgs')], false)).toBe('S1');
    expect(alignmentTrackName(entry('S1', 'unspecified'), [entry('S1', 'unspecified'), entry('S1', 'pgt')])).toBe(
      'S1 (unspecified)',
    );
  });
});
