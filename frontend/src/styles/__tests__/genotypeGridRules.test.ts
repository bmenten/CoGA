import { describe, expect, it } from 'vitest';

import { themeRules } from './themeSource';

/**
 * The genotype grid every variant card shares (FamilyGenotypeGrid): a sample ID is never broken
 * across lines. On a narrow card the role and the call wrap between their words instead (a
 * monogenic NIPT row reads "plasma (cfDNA)" and "no call (reference)"), so the sample column is
 * never squeezed to a letter a line. jsdom lays nothing out, so this reads the rules themselves.
 */

const RULES = themeRules();

const declarationOf = (selector: string, property: string): string | null => {
  const rule = RULES.find(({ selectors }) => selectors.split(',').some((one) => one.trim() === selector));
  return rule ? (new RegExp(`(?:^|[;\\s])${property}:\\s*([^;]+)`).exec(rule.body)?.[1]?.trim() ?? null) : null;
};

describe('the genotype grid', () => {
  it('never breaks a sample ID', () => {
    expect(declarationOf('.variant-card-gtline-sample', 'white-space')).toBe('nowrap');
    expect(declarationOf('.variant-card-gtline-sample', 'overflow-wrap')).toBeNull();
    // The sample's track takes its whole ID; the grid does not let every value break anywhere.
    expect(declarationOf('.variant-card-gtlist', 'grid-template-columns')).toMatch(/^max-content /);
    expect(declarationOf('.variant-card-gtlist', 'overflow-wrap')).toBeNull();
  });

  it('lets the role and the call give way, between their words', () => {
    expect(declarationOf('.variant-card-gtlist', 'grid-template-columns')).toContain(
      'repeat(var(--gt-cols, 5), minmax(min-content, max-content))',
    );
    // A note under a call never widens the value columns.
    expect(declarationOf('.variant-card-gtline-detail', 'overflow-wrap')).toBe('anywhere');
  });
});
