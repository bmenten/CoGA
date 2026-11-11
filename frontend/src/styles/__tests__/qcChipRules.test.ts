import { describe, expect, it } from 'vitest';

import { themeRules } from './themeSource';

/**
 * The sequencing-QC chip of the family members table. jsdom applies no
 * stylesheet, so these read the rules themselves.
 *
 * - The chip that opens a report is a `<button>`, and the shared `button:hover` rule, (0,1,1),
 *   outranks a tone class such as `.table-chip--warning`, (0,1,0): without a rule of its own a
 *   hovered Warning or Fail chip would turn dark grey with white text. Each tone's hover rule
 *   restates its colours.
 * - Edited, the table's columns are fixed: a chip wider than its column wraps inside it,
 *   rather than running under the Remove button beside it.
 */

const RULES = themeRules();

/** The declarations of every rule whose selector list holds `selector`, joined. */
const declarationsFor = (selector: string): string =>
  RULES.filter(({ selectors }) => selectors.split(',').some((one) => one.replace(/\s+/g, ' ').trim() === selector))
    .map(({ body }) => body)
    .join(';');

const declares = (body: string, property: string): boolean =>
  new RegExp(`(^|[;\\s])${property}\\s*:`).test(body);

describe('the sequencing-QC chip', () => {
  it.each(['warning', 'critical', 'neutral'])('keeps its %s tone when hovered', (tone) => {
    const hovered = declarationsFor(`.family-qc-chip.table-chip--${tone}:hover:not(:disabled)`);
    expect(declares(hovered, 'background')).toBe(true);
    expect(declares(hovered, 'color')).toBe(true);
  });

  it('wraps inside its column when the members table is edited', () => {
    for (const selector of [
      '.family-members-table--editing .family-qc-chip',
      '.family-members-table--editing .family-qc-chip-static',
    ]) {
      const rule = declarationsFor(selector);
      expect(rule).toMatch(/max-width:\s*100%/);
      expect(rule).toMatch(/white-space:\s*normal/);
    }
  });
});
