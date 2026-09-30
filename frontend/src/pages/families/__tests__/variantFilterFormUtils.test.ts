import { describe, expect, it, vi } from 'vitest';
import type { SyntheticEvent } from 'react';

import {
  countNonEmpty,
  formatPercentFilterValue,
  normalizePercentValue,
  stopSummaryInteraction,
} from '../variantFilterFormUtils';

describe('countNonEmpty', () => {
  it('counts the filled-in values, ignoring whitespace', () => {
    expect(countNonEmpty('a', '', '  ', 'b')).toBe(2);
    expect(countNonEmpty()).toBe(0);
  });
});

describe('formatPercentFilterValue', () => {
  it('shows a fraction as a percent without trailing zeros', () => {
    expect(formatPercentFilterValue('0.001')).toBe('0.1%');
    expect(formatPercentFilterValue('0.01')).toBe('1%');
    expect(formatPercentFilterValue('0.0005')).toBe('0.05%');
  });

  it('shows "Any" for no threshold', () => {
    expect(formatPercentFilterValue('')).toBe('Any');
    expect(formatPercentFilterValue('0')).toBe('Any');
    expect(formatPercentFilterValue('abc')).toBe('Any');
  });
});

describe('normalizePercentValue', () => {
  it('reads the value as a number held within 0–10', () => {
    expect(normalizePercentValue('0.5')).toBe(0.5);
    expect(normalizePercentValue('-1')).toBe(0);
    expect(normalizePercentValue('abc')).toBe(0);
    expect(normalizePercentValue('25')).toBe(10);
  });
});

describe('stopSummaryInteraction', () => {
  it('stops the event from toggling the section', () => {
    const stopPropagation = vi.fn();
    stopSummaryInteraction({ stopPropagation } as unknown as SyntheticEvent<HTMLElement>);
    expect(stopPropagation).toHaveBeenCalledOnce();
  });
});
