import { describe, expect, it } from 'vitest';

import { themeRules } from './themeSource';

/**
 * A small variant's card (small variants, monogenic NIPT) takes ClinVar's colour as its
 * background: light red for (likely) pathogenic, light orange for a conflict that lists a
 * (likely) pathogenic classification, light green for (likely) benign. The other
 * findings' cards share the rail classes (FindingCard: a pathogenic repeat, an mtDNA variant)
 * but keep the white card. jsdom applies no stylesheet, so this reads the rules themselves.
 */

const RULES = themeRules();

const rulesFor = (selector: string) =>
  RULES.filter(({ selectors }) => selectors.split(',').some((one) => one.trim() === selector));

const backgroundOf = (selector: string): string | null => {
  const bodies = rulesFor(selector).map(({ body }) => /(?:^|[;\s])background(?:-color)?:\s*([^;]+)/.exec(body));
  return bodies.reverse().find(Boolean)?.[1]?.trim() ?? null;
};

const tokenValue = (name: string): string | null => {
  const root = RULES.filter(({ selectors }) => selectors.trim() === ':root').map(({ body }) => body).join(';');
  return new RegExp(`${name}:\\s*([^;]+)`).exec(root)?.[1]?.trim() ?? null;
};

describe("the ClinVar tint of a small variant's card", () => {
  it('fills a (likely) pathogenic card light red, a conflict with P/LP light orange, a (likely) benign one light green', () => {
    expect(backgroundOf('.small-variant-card.variant-card--clinvar-pathogenic')).toBe(
      'var(--color-clinvar-pathogenic-surface)',
    );
    expect(backgroundOf('.small-variant-card.variant-card--clinvar-benign')).toBe(
      'var(--color-clinvar-benign-surface)',
    );
    expect(backgroundOf('.small-variant-card.variant-card--clinvar-warning')).toBe('var(--color-warning-surface)');
    expect(tokenValue('--color-clinvar-pathogenic-surface')).toMatch(/^#[0-9a-f]{6}$/i);
    expect(tokenValue('--color-warning-surface')).toMatch(/^#[0-9a-f]{6}$/i);
    expect(tokenValue('--color-clinvar-benign-surface')).toMatch(/^#[0-9a-f]{6}$/i);
  });

  it('leaves every other classification, and every other finding, on the white card', () => {
    expect(backgroundOf('.small-variant-card.variant-card--clinvar-neutral')).toBeNull();
    expect(backgroundOf('.variant-card--clinvar-pathogenic')).toBeNull();
    expect(backgroundOf('.variant-card--clinvar-benign')).toBeNull();
    expect(backgroundOf('.variant-card--clinvar-warning')).toBeNull();
  });
});
