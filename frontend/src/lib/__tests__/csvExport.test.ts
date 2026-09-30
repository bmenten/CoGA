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

  it('reads a truncation without a reason (older backend) as the row cap', () => {
    const info = describeCsvExport(
      { headers: { 'x-coga-export-truncated': 'true', 'x-coga-export-limit': '50000' } },
      'x',
    );
    expect(info.reason).toBe('row-limit');
    expect(info.filename).toBe('x-TRUNCATED-first-50000.csv');
  });

  it('treats a response without the headers as complete (older backend)', () => {
    expect(describeCsvExport({ headers: {} }, 'x').truncated).toBe(false);
  });
});
