// One writer of a sequencing-QC value, for the members table's chip and a signed version's
// sequencing QC (ENG-10): the signed report wrote "18.57 x" where the chip wrote "18.6x".

import { describe, expect, it } from 'vitest';

import { formatQcLimit, formatQcMetricValue, lowerIsWorse } from '../qcMetricValue';

describe('formatQcMetricValue', () => {
  it('writes each unit as the chip does', () => {
    const passing = { verdict: 'pass' };
    expect(formatQcMetricValue({ ...passing, value: 18.57, unit: 'x' })).toBe('18.6x');
    expect(formatQcMetricValue({ ...passing, value: 6.44, unit: '%' })).toBe('6.4%');
    expect(formatQcMetricValue({ ...passing, value: 32.4, unit: 'Q' })).toBe('Q32');
    expect(formatQcMetricValue({ ...passing, value: 0.12345, unit: 'fraction' })).toBe('0.123');
    expect(formatQcMetricValue({ ...passing, value: 2.5, unit: 'Mb' })).toBe('2.5 Mb');
  });

  it('keeps a breaching value on the failing side of the limit it crossed', () => {
    expect(
      formatQcMetricValue({ value: 19.96, unit: 'x', verdict: 'warn', warn_value: 20, direction: 'lower_is_worse' }),
    ).toBe('19.96x');
    expect(
      formatQcMetricValue({ value: 30.04, unit: '%', verdict: 'fail', error_value: 30, direction: 'higher_is_worse' }),
    ).toBe('30.04%');
  });
});

describe('lowerIsWorse', () => {
  it('takes the served direction, and without it the side of the limit the value lies on', () => {
    expect(lowerIsWorse({ value: 25, unit: 'x', verdict: 'pass', direction: 'lower_is_worse' })).toBe(true);
    expect(lowerIsWorse({ value: 19, unit: 'x', verdict: 'warn', warn_value: 20 })).toBe(true);
    expect(lowerIsWorse({ value: 31, unit: '%', verdict: 'fail', error_value: 30 })).toBe(false);
  });
});

describe('formatQcLimit', () => {
  it('writes a limit exactly as configured, in its unit', () => {
    expect(formatQcLimit('x', 20)).toBe('20x');
    expect(formatQcLimit('%', 2.25)).toBe('2.25%');
  });
});
