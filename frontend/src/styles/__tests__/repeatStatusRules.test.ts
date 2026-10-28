import { describe, expect, it } from 'vitest';

import type { ApiRepeatStatus } from '../../lib/apiTypes';
import { REPEAT_STATUS_TONE } from '../../pages/families/FamilyRepeatExpansionsPage';
import { STATUS_CHIP_TONE_CLASS } from '../../pages/families/FamilyGenotypeGrid';
import { themeText } from './themeSource';

/**
 * Every repeat status has a style of its own on the repeat page: a call's alleles, and the
 * chip a locus shows beside its name for any status but normal. A *review* call had neither,
 * and read as a plain chip (ENG-10, #826). The repeat cards colour both through the tones every
 * variant card shares (FamilyGenotypeGrid), so this checks each status's tone has its rules.
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

const declarationOf = (selector: string, property: 'color' | 'opacity'): string | null => {
  const rule = [...THEME.matchAll(/([^{}]+)\{([^}]*)\}/g)].find(([, selectors]) =>
    selectors.split(',').some((one) => one.trim() === selector),
  );
  return rule ? (new RegExp(`(?:^|[;\\s])${property}:\\s*([^;]+)`).exec(rule[2])?.[1]?.trim() ?? null) : null;
};

const callColour = (status: ApiRepeatStatus) =>
  declarationOf(`.variant-card-gtline--${REPEAT_STATUS_TONE[status]} .variant-card-gtline-gt`, 'color');

describe('the repeat statuses', () => {
  it('lists every status the API serves', () => {
    expect(listsEveryStatus).toBe(true);
  });

  it.each(STATUSES)('styles a %s call', (status) => {
    const tone = REPEAT_STATUS_TONE[status];
    // A call without a status of its own (unknown) is faded rather than coloured.
    expect(callColour(status) ?? declarationOf(`.variant-card-gtline--${tone}`, 'opacity')).not.toBeNull();
  });

  it.each(STATUSES.filter((status) => status !== 'normal'))('styles the locus chip of a %s call', (status) => {
    const chip = STATUS_CHIP_TONE_CLASS[REPEAT_STATUS_TONE[status]];
    expect(declarationOf(`.variant-card-chip--${chip}`, 'color')).not.toBeNull();
  });

  it('gives a review call a colour no other status has', () => {
    const review = callColour('review');
    expect(review).not.toBeNull();
    const others = STATUSES.filter((status) => status !== 'review').map(callColour);
    expect(others).not.toContain(review);
    const reviewChip = STATUS_CHIP_TONE_CLASS[REPEAT_STATUS_TONE.review];
    expect(
      STATUSES.filter((status) => status !== 'review').map(
        (status) => STATUS_CHIP_TONE_CLASS[REPEAT_STATUS_TONE[status]],
      ),
    ).not.toContain(reviewChip);
  });
});
