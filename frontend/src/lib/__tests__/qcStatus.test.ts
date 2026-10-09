import { describe, expect, it } from 'vitest';

import { worstIntegrityStatus, worstQcStatus } from '../qcStatus';

describe('QC roll-ups', () => {
  it('keeps the plain severity order, in which a check not run ranks lowest', () => {
    expect(worstQcStatus(['skip', 'pass'])).toBe('pass');
    expect(worstQcStatus(['pass', 'warn', 'skip'])).toBe('warn');
    expect(worstQcStatus(['warn', 'fail'])).toBe('fail');
    expect(worstQcStatus([])).toBe('skip');
  });

  it('counts a sample-integrity check that could not run as a warning, never a pass (CLIN-1)', () => {
    expect(worstIntegrityStatus(['skip', 'pass'])).toBe('warn');
    expect(worstIntegrityStatus(['skip'])).toBe('warn');
    expect(worstIntegrityStatus(['pass', 'pass'])).toBe('pass');
    // A fail still outranks it.
    expect(worstIntegrityStatus(['skip', 'fail'])).toBe('fail');
    // Only no check at all is not run.
    expect(worstIntegrityStatus([])).toBe('skip');
  });
});
