import { describe, expect, it } from 'vitest';

import {
  formatCount,
  formatDate,
  formatDateTime,
  formatRegionSize,
  formatTimestamp,
  joinWithAnd,
} from '../format';

describe('formatDateTime', () => {
  it('shows a timestamp in the locale and an unparseable value as it came', () => {
    const iso = '2026-09-30T10:15:00Z';
    expect(formatDateTime(iso)).toBe(new Date(iso).toLocaleString());
    expect(formatDateTime('not a date')).toBe('not a date');
  });

  it('shows the caller’s text for a missing value, blank by default', () => {
    expect(formatDateTime(null)).toBe('');
    expect(formatDateTime('')).toBe('');
    expect(formatDateTime(undefined, 'Not synced')).toBe('Not synced');
    expect(formatTimestamp(null)).toBe('—');
  });
});

describe('formatDate', () => {
  it('shows a date-only value in UTC, so it does not fall a day early west of UTC (#526)', () => {
    expect(formatDate('2026-06-06')).toBe(
      new Date('2026-06-06').toLocaleDateString(undefined, { timeZone: 'UTC' }),
    );
  });

  it('shows a timestamp’s local date, and the missing text for no value', () => {
    const iso = '2026-06-06T23:30:00Z';
    expect(formatDate(iso)).toBe(new Date(iso).toLocaleDateString());
    expect(formatDate(null, 'Not installed')).toBe('Not installed');
    expect(formatDate('garbage')).toBe('garbage');
  });
});

describe('formatCount', () => {
  it('groups digits as the locale does', () => {
    expect(formatCount(1234567)).toBe(new Intl.NumberFormat().format(1234567));
  });

  it('does not throw on a count the payload left out', () => {
    expect(formatCount(undefined as unknown as number)).toBe('NaN');
  });
});

describe('formatRegionSize', () => {
  it('uses bp below 1 kb, one decimal in kb and two in Mb', () => {
    expect(formatRegionSize(999)).toBe('999 bp');
    expect(formatRegionSize(1_500)).toBe('1.5 kb');
    expect(formatRegionSize(2_345_678)).toBe('2.35 Mb');
  });
});

describe('joinWithAnd', () => {
  it('joins lists in prose form', () => {
    expect(joinWithAnd([])).toBe('');
    expect(joinWithAnd(['a'])).toBe('a');
    expect(joinWithAnd(['a', 'b'])).toBe('a and b');
    expect(joinWithAnd(['a', 'b', 'c'])).toBe('a, b and c');
  });

  it('leaves out empty entries', () => {
    expect(joinWithAnd(['a', '', 'b'])).toBe('a and b');
    expect(joinWithAnd(['', 'a'])).toBe('a');
  });
});
