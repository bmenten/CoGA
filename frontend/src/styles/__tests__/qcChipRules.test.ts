import { describe, expect, it } from 'vitest';

import { themeText } from './themeSource';

/**
 * The sequencing-QC chip of the family members table (ENG-10, #867). jsdom applies no
 * stylesheet, so these read the rules themselves; the behaviour was checked in a browser
 * against the stylesheet before and after.
 *
 * - The chip that opens a report is a `<button>`, and the shared `button:hover` rule, (0,1,1),
 *   outranks a tone class such as `.table-chip--warning`, (0,1,0): a hovered Warning or Fail
 *   chip turned dark grey with white text. Each tone's hover rule restates its colours.
 * - Edited, the table's columns are fixed, and a chip wider than its column ran under the
 *   Remove button beside it. It wraps inside its column instead.
 */

const THEME = themeText().replace(/\/\*[\s\S]*?\*\//g, '');

/** The declarations of every rule whose selector list holds `selector`, joined. */
const declarationsFor = (selector: string): string =>
  [...THEME.matchAll(/([^{}]+)\{([^}]*)\}/g)]
    .filter(([, selectors]) => selectors.split(',').some((one) => one.replace(/\s+/g, ' ').trim() === selector))
    .map(([, , body]) => body)
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
