// Tooltip values are escaped — #521: d3 tooltips interpolated imported strings as HTML.

import { describe, expect, it } from 'vitest';

import { escapeHtml } from '../escapeHtml';

describe('escapeHtml', () => {
  it('escapes markup characters', () => {
    expect(escapeHtml('<img src=x onerror=alert(1)>')).toBe('&lt;img src=x onerror=alert(1)&gt;');
    expect(escapeHtml(`"quoted" & 'single'`)).toBe('&quot;quoted&quot; &amp; &#39;single&#39;');
  });

  it('turns absent values into an empty string', () => {
    expect(escapeHtml(undefined)).toBe('');
    expect(escapeHtml(null)).toBe('');
    expect(escapeHtml(17)).toBe('17');
  });
});
