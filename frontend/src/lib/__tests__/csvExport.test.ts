import { describe, expect, it } from 'vitest';

import { describeCsvExport, truncatedExportMessage } from '../csvExport';

describe('describeCsvExport (#512)', () => {
  it('keeps the plain name for a complete export', () => {
    const info = describeCsvExport(
      { headers: { 'x-coga-export-truncated': 'false', 'x-coga-export-rows': '12', 'x-coga-export-limit': '50000' } },
      'family-F1-small-variants',
    );
    expect(info).toEqual({
      filename: 'family-F1-small-variants.csv',
      truncated: false,
      reason: null,
      rows: 12,
      limit: 50000,
    });
  });

  it('names and describes a capped export so it cannot pass for the full result', () => {
    const info = describeCsvExport(
      { headers: { 'x-coga-export-truncated': 'true', 'x-coga-export-rows': '50000', 'x-coga-export-limit': '50000' } },
      'family-F1-small-variants',
    );
    expect(info.filename).toBe('family-F1-small-variants-TRUNCATED-first-50000.csv');
    expect(info.truncated).toBe(true);
    expect(info.reason).toBe('row-limit');
    expect(truncatedExportMessage(info)).toMatch(/capped at 50,000 rows .* incomplete/);
  });

  it('names and describes an export from a capped candidate read as a partial search', () => {
    // A compound-het / recessive / carrier search reads a capped candidate window: the
    // file holds far fewer rows than the export cap and is still incomplete.
    const info = describeCsvExport(
      {
        headers: {
          'x-coga-export-truncated': 'true',
          'x-coga-export-truncated-reason': 'candidate-limit',
          'x-coga-export-rows': '37',
          'x-coga-export-limit': '50000',
        },
      },
      'family-F1-small-variants',
    );
    expect(info.filename).toBe('family-F1-small-variants-TRUNCATED-partial-search.csv');
    expect(info.truncated).toBe(true);
    expect(info.reason).toBe('candidate-limit');
    const message = truncatedExportMessage(info);
    expect(message).toMatch(/matching variants may be missing from the file \(37 rows\) — it is incomplete/);
    expect(message).not.toMatch(/capped at 50,000/);
  });

  it('names and describes an export through a capped tag or classification filter as a partial search', () => {
    // The Variant Explorer searches at most a fixed number of the variants its tag or
    // classification filter matched: matches can be missing however few rows the file holds,
    // and a narrower region or panel does not help (DATA-2).
    const info = describeCsvExport(
      {
        headers: {
          'x-coga-export-truncated': 'true',
          'x-coga-export-truncated-reason': 'review-filter-limit',
          'x-coga-export-rows': '12',
          'x-coga-export-limit': '50000',
        },
      },
      'variant-explorer-asm-38',
    );
    expect(info.filename).toBe('variant-explorer-asm-38-TRUNCATED-partial-search.csv');
    expect(info.truncated).toBe(true);
    expect(info.reason).toBe('review-filter-limit');
    const message = truncatedExportMessage(info);
    expect(message).toMatch(
      /tag or classification filter matched more variants than one search can take, so matching variants may be missing from the file \(12 rows\) — it is incomplete/,
    );
    expect(message).toMatch(/Filter on fewer tags or classifications to export everything/);
    expect(message).not.toMatch(/capped at 50,000|a region, a gene panel/);
  });

  it('reads a truncation without a reason (older backend) as the row cap', () => {
    const info = describeCsvExport(
      { headers: { 'x-coga-export-truncated': 'true', 'x-coga-export-limit': '50000' } },
      'x',
    );
    expect(info.reason).toBe('row-limit');
    expect(info.filename).toBe('x-TRUNCATED-first-50000.csv');
    // So is a reason this page does not know: the file is still named and announced as cut.
    const unknown = describeCsvExport(
      { headers: { 'x-coga-export-truncated': 'true', 'x-coga-export-truncated-reason': 'later-reason' } },
      'x',
    );
    expect(unknown.reason).toBe('row-limit');
    expect(unknown.truncated).toBe(true);
  });

  it('treats a response without the headers as complete (older backend)', () => {
    expect(describeCsvExport({ headers: {} }, 'x').truncated).toBe(false);
  });
});
