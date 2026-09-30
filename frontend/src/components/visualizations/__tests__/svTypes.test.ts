import { describe, expect, it } from 'vitest';

import { SV_TYPE_ORDER, describeSvTypes } from '../svTypes';

describe('describeSvTypes', () => {
  it('counts the drawn SVs by type, in track order (#529)', () => {
    expect(
      describeSvTypes([{ typeKey: 'DUP' }, { typeKey: 'DEL' }, { typeKey: 'DEL' }]),
    ).toBe('3 (2 DEL, 1 DUP)');
  });

  it('keeps the row order the tracks draw', () => {
    expect(SV_TYPE_ORDER).toEqual(['DEL', 'DUP', 'INV', 'INS', 'BND']);
  });
});
