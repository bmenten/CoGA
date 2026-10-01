import { describe, expect, it } from 'vitest';

import { clamp, isFiniteNumber } from '../number';

describe('clamp', () => {
  it('holds a value within its bounds', () => {
    expect(clamp(5, 0, 10)).toBe(5);
    expect(clamp(-1, 0, 10)).toBe(0);
    expect(clamp(11, 0, 10)).toBe(10);
    expect(clamp(0, 0, 10)).toBe(0);
    expect(clamp(10, 0, 10)).toBe(10);
  });
});

describe('isFiniteNumber', () => {
  it('accepts a finite number, zero and negatives included', () => {
    expect(isFiniteNumber(0)).toBe(true);
    expect(isFiniteNumber(-0.5)).toBe(true);
    expect(isFiniteNumber(0.987)).toBe(true);
  });

  it('rejects NaN, the infinities and anything that is not a number', () => {
    for (const value of [NaN, Infinity, -Infinity, undefined, null, '0.9', true, {}]) {
      expect(isFiniteNumber(value)).toBe(false);
    }
  });

  it('narrows a list of optional numbers to its finite numbers', () => {
    const xs: (number | undefined)[] = [3, undefined, NaN, 1];
    const finite: number[] = xs.filter(isFiniteNumber);
    expect(finite).toEqual([3, 1]);
  });
});
