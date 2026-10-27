import { describe, expect, it } from 'vitest';

import type { ApiRepeatStatus } from '../../lib/apiTypes';
import { themeText } from './themeSource';

/**
 * Every repeat status has a style of its own on the repeat page: a call's status word, and
 * the chip a locus shows beside its name for any status but normal. A *review* call had
 * neither, and read as a plain table chip, though its row has its own rail (ENG-10, #826).
 * jsdom applies no stylesheet, so this reads the rules themselves.
 */

const STATUSES = [
  'normal',
  'review',
  'not_assessed',
  'intermediate',
  'premutation',
  'pathogenic',
  'unknown',
] as const satisfies readonly ApiRepeatStatus[];
// A status the API adds fails the type check here until it is listed above, and styled.
const listsEveryStatus: Exclude<ApiRepeatStatus, (typeof STATUSES)[number]> extends never ? true : false = true;

const THEME = themeText().replace(/\/\*[\s\S]*?\*\//g, '');

const colourOf = (selector: string): string | null => {
  const rule = [...THEME.matchAll(/([^{}]+)\{([^}]*)\}/g)].find(([, selectors]) =>
    selectors.split(',').some((one) => one.trim() === selector),
  );
  return rule ? (/(?:^|[;\s])color:\s*([^;]+)/.exec(rule[2])?.[1]?.trim() ?? null) : null;
};

describe('the repeat statuses', () => {
  it('lists every status the API serves', () => {
    expect(listsEveryStatus).toBe(true);
  });

  it.each(STATUSES)('colours a %s call', (status) => {
    expect(colourOf(`.repeat-status--${status}`)).not.toBeNull();
  });

  it.each(STATUSES.filter((status) => status !== 'normal'))('styles the locus chip of a %s call', (status) => {
    expect(colourOf(`.family-repeat-table-locus-flag--${status}`)).not.toBeNull();
  });

  it('gives a review call a colour no other status has', () => {
    const others = STATUSES.filter((status) => status !== 'review').map((status) => colourOf(`.repeat-status--${status}`));
    expect(others).not.toContain(colourOf('.repeat-status--review'));
  });
});
