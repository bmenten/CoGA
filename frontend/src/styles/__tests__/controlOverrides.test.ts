import { readFileSync } from 'node:fs';
import path from 'node:path';
import { describe, expect, it } from 'vitest';

/**
 * The shared form-control rule is written as
 *
 *   input:not([type='checkbox']):not([type='radio']) { min-height: 3.15rem; padding: 1rem }
 *
 * `:not()` takes the specificity of its argument, so each attribute selector adds a
 * class-level weight: the selector scores **(0,2,1)**, not (0,0,1) as its shape
 * suggests. A two-class override like `.some-table .some-input` scores (0,2,0) and
 * *loses* — silently, with no warning anywhere, leaving the control at its 50px
 * login-form height.
 *
 * That is exactly what happened to the QC threshold inputs: three rounds of tuning
 * padding, width and height had no effect at all, because none of it applied. jsdom
 * does not apply external stylesheets, so no component test can catch it either.
 *
 * This asserts that every override of that rule carries enough specificity to win.
 */

const THEME = readFileSync(path.resolve(process.cwd(), 'src/styles/theme.css'), 'utf8')
  // Comments sit between rules and would otherwise be read as part of the next selector.
  .replace(/\/\*[\s\S]*?\*\//g, '');

const SHARED_RULE = "input:not([type='checkbox']):not([type='radio'])";
const SHARED_RULE_AT = THEME.indexOf(SHARED_RULE);

/**
 * Rules that lose to the shared control rule and predate this check. Each is a latent
 * version of the same defect — the size it sets is not applied — but confirming what
 * each *should* look like needs the page in front of you, so they are recorded rather
 * than changed blind. Removing an entry here means fixing the rule.
 */
const KNOWN_UNAPPLIED = new Set([
  '.table-filter-row input, .table-filter-row select',
  '.family-paraphase-filter-field input',
  '.family-mtdna-filter-field input, .family-mtdna-filter-field select',
  '.family-mtdna-filter-toggle input',
  '.cnv-points-input',
]);

interface ControlRule {
  selector: string;
  at: number;
}

/** Every rule that tries to resize a form control. */
const controlOverrides = (): ControlRule[] => {
  const rules: ControlRule[] = [];
  const pattern = /([^{}]+)\{([^}]*)\}/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(THEME)) !== null) {
    const [, selector, body] = match;
    if (!/\binput\b/.test(selector)) continue;
    // Only rules that try to resize the control; colour-only rules are unaffected.
    if (!/(min-)?height|padding/.test(body)) continue;
    rules.push({ selector: selector.replace(/\s+/g, ' ').trim(), at: match.index });
  }
  return rules;
};

/**
 * Rough CSS specificity, counting `:not(...)` by its argument as the spec requires.
 * Good enough to compare against the one rule that matters here.
 */
const specificity = (selector: string): [number, number, number] => {
  let working = selector;
  let classes = 0;
  let elements = 0;

  // :not(...) contributes its argument's specificity, not its own.
  working = working.replace(/:not\(([^)]*)\)/g, (_full, inner: string) => {
    const [, innerClasses, innerElements] = specificity(inner);
    classes += innerClasses;
    elements += innerElements;
    return ' ';
  });

  classes += (working.match(/\.[a-zA-Z_-][\w-]*/g) ?? []).length;
  classes += (working.match(/\[[^\]]+\]/g) ?? []).length;
  classes += (working.match(/:[a-zA-Z-]+/g) ?? []).length;
  elements += (working.match(/(^|[\s>+~])[a-zA-Z][\w-]*/g) ?? []).length;
  return [0, classes, elements];
};

/** -1, 0 or 1, comparing two specificities. */
const compare = (a: [number, number, number], b: [number, number, number]): number => {
  for (let i = 0; i < 3; i += 1) {
    if (a[i] !== b[i]) return a[i] > b[i] ? 1 : -1;
  }
  return 0;
};

const beats = (a: [number, number, number], b: [number, number, number]): boolean =>
  compare(a, b) > 0;

/** A selector the shared rule never matches in the first place. */
const outOfScope = (selector: string): boolean =>
  /\[type=['"]?(checkbox|radio)['"]?\]/.test(selector);

describe('form-control size overrides', () => {
  it('scores the shared control rule the way the browser does', () => {
    // (0,2,1): one element plus two attribute selectors inside :not(). Getting this
    // wrong is the whole reason the QC threshold inputs stayed at their default size.
    expect(specificity(SHARED_RULE)).toEqual([0, 2, 1]);
    // The shape it is usually mistaken for.
    expect(beats(specificity('.a-table .an-input'), specificity(SHARED_RULE))).toBe(false);
  });

  it('gives every control resize enough specificity to actually apply', () => {
    const shared = specificity(SHARED_RULE);
    const losers = controlOverrides()
      .filter(({ selector }) => !selector.includes(SHARED_RULE))
      .filter(({ selector }) => !KNOWN_UNAPPLIED.has(selector))
      .filter(({ selector, at }) =>
        selector
          .split(',')
          .some((part) => {
            if (!part.includes('input') || outOfScope(part)) return false;
            const order = compare(specificity(part), shared);
            // Equal specificity is decided by source order, so a later rule still wins.
            return order < 0 || (order === 0 && at < SHARED_RULE_AT);
          }),
      )
      .map(({ selector }) => selector);

    expect(losers, [
      'These rules set a height/padding on an input but lose to the shared control rule',
      `(${SHARED_RULE}), which scores (0,2,1) because :not() carries its argument's`,
      'specificity. They will have no effect. Add `input` and the same `:not([type=...])`',
      'guards to the selector, or scope it under one more class.',
    ].join(' ')).toEqual([]);
  });
});
