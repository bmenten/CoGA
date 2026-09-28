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
    expect(truncatedExportMessage(info)).toMatch(/capped at 50,000 rows .* incomplete/);
  });

  it('treats a response without the headers as complete (older backend)', () => {
    expect(describeCsvExport({ headers: {} }, 'x').truncated).toBe(false);
  });
});
