// @vitest-environment node
import { readFileSync, readdirSync } from 'node:fs';
import path from 'node:path';
import postcss, { type Rule } from 'postcss';
import ts from 'typescript';
import { describe, expect, it } from 'vitest';

/**
 * theme.css grew to 266 KB because nothing ever removed a rule once the markup it styled was
 * gone: 331 rules named classes that no component rendered any more (#707). This keeps the
 * stylesheet honest. Every class a selector names must be one the source can put in the DOM,
 * and every custom property it defines must be read somewhere.
 *
 * "Can put in the DOM" is read from the TypeScript AST of every non-test source file:
 * - every string literal contributes its whitespace-separated words;
 * - a template literal or `+` concatenation whose interpolations have known values
 *   (`${open ? ' is-open' : ''}`) is expanded;
 * - where an interpolation's value is unknown (`repeat-status--${status}`), the word it
 *   touches becomes a prefix or suffix that any class may extend.
 * That errs towards keeping a rule: a word that is not a class only makes a selector look
 * used.
 */

const SRC = path.resolve(process.cwd(), 'src');
const THEME_PATH = path.join(SRC, 'styles/theme.css');

/**
 * Open fragments that are not class names but, as prefixes, would excuse every class they
 * begin. Each entry says where it comes from; the last test fails when one is no longer
 * produced, so the list cannot go stale.
 */
const NOT_CLASS_FRAGMENTS: Record<string, string> = {
  'family-':
    'CSV export file names, `family-${familyId}-small-variants` (SmallVariantResults, StructuralVariantResults)',
  variant:
    'ACMG evidence prose, `proband carries the variant${afNote}` (lib/acmg/evaluateMito.ts)',
};

/** JSX attributes whose values are never class names. */
const NON_CLASS_ATTRIBUTES =
  /^(key|id|htmlFor|href|to|src|alt|title|placeholder|role|type|name|value|download|target|rel|aria-.*|data-.*)$/;

const WORD = /^-?[A-Za-z_][\w-]*$/;
const CLASS = /\.(-?[_a-zA-Z][\w-]*)/g;
const MAX_VARIANTS = 64;

interface SourceWords {
  whole: Set<string>;
  /** Open fragments, including the NOT_CLASS_FRAGMENTS (excluded when matching). */
  prefixes: Set<string>;
  suffixes: Set<string>;
  /** Every string literal, for custom-property names read from code. */
  literals: Set<string>;
}

const sourceFiles = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory())
      return entry.name === '__tests__' ? [] : sourceFiles(full);
    return /\.tsx?$/.test(entry.name) && !/\.(test|d)\.tsx?$/.test(entry.name)
      ? [full]
      : [];
  });

const unwrap = (node: ts.Expression): ts.Expression => {
  let current = node;
  while (
    ts.isParenthesizedExpression(current) ||
    ts.isAsExpression(current) ||
    ts.isNonNullExpression(current)
  ) {
    current = current.expression;
  }
  return current;
};

/** The values an expression can take, or null when they are not all known. */
const valuesOf = (node: ts.Expression): string[] | null => {
  const expr = unwrap(node);
  if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr))
    return [expr.text];
  if (ts.isConditionalExpression(expr)) {
    const whenTrue = valuesOf(expr.whenTrue);
    const whenFalse = valuesOf(expr.whenFalse);
    return whenTrue && whenFalse
      ? [...whenTrue, ...whenFalse].slice(0, MAX_VARIANTS)
      : null;
  }
  if (ts.isTemplateExpression(expr)) {
    let acc = [expr.head.text];
    for (const span of expr.templateSpans) {
      const inner = valuesOf(span.expression);
      if (!inner) return null;
      acc = acc
        .flatMap((left) =>
          inner.map((value) => `${left}${value}${span.literal.text}`)
        )
        .slice(0, MAX_VARIANTS);
    }
    return acc;
  }
  if (
    ts.isBinaryExpression(expr) &&
    expr.operatorToken.kind === ts.SyntaxKind.PlusToken
  ) {
    const left = valuesOf(expr.left);
    const right = valuesOf(expr.right);
    if (!left || !right) return null;
    return left.flatMap((l) => right.map((r) => l + r)).slice(0, MAX_VARIANTS);
  }
  return null;
};

type Part = string | ts.Expression;

/** A template literal or `+` chain as literal text and interpolated expressions. */
const partsOf = (node: ts.Expression): Part[] => {
  const expr = unwrap(node);
  if (
    ts.isBinaryExpression(expr) &&
    expr.operatorToken.kind === ts.SyntaxKind.PlusToken
  ) {
    return [...partsOf(expr.left), ...partsOf(expr.right)];
  }
  if (ts.isStringLiteral(expr) || ts.isNoSubstitutionTemplateLiteral(expr))
    return [expr.text];
  if (ts.isTemplateExpression(expr)) {
    return [
      expr.head.text,
      ...expr.templateSpans.flatMap((span): Part[] => [
        span.expression,
        span.literal.text,
      ]),
    ];
  }
  return [expr];
};

const addWords = (
  text: string,
  words: SourceWords,
  openLeft: boolean,
  openRight: boolean
): void => {
  const pieces = text.split(/\s+/);
  pieces.forEach((piece, index) => {
    if (!piece) return;
    const leftOpen = index === 0 && openLeft;
    const rightOpen = index === pieces.length - 1 && openRight;
    if (rightOpen) words.prefixes.add(piece);
    if (leftOpen) words.suffixes.add(piece);
    if (!leftOpen && !rightOpen && WORD.test(piece)) words.whole.add(piece);
  });
};

/**
 * Split a template/concatenation at its unknown interpolations; each run between them has
 * only known parts, so expand it and read its words, leaving the edge words open where an
 * unknown value touches them.
 */
const addComposite = (parts: Part[], words: SourceWords): void => {
  const runs: {
    variants: string[];
    unknownBefore: boolean;
    unknownAfter: boolean;
  }[] = [];
  let current: string[] = [''];
  let unknownBefore = false;
  const flush = (unknownAfter: boolean) => {
    runs.push({ variants: current, unknownBefore, unknownAfter });
    current = [''];
    unknownBefore = unknownAfter;
  };
  for (const part of parts) {
    const values = typeof part === 'string' ? [part] : valuesOf(part);
    if (values === null) {
      flush(true);
      continue;
    }
    current = current
      .flatMap((left) => values.map((value) => left + value))
      .slice(0, MAX_VARIANTS);
  }
  flush(false);
  for (const run of runs) {
    for (const text of run.variants) {
      addWords(
        text,
        words,
        run.unknownBefore && !/^\s/.test(text),
        run.unknownAfter && !/\s$/.test(text)
      );
    }
  }
};

const isPlus = (node: ts.Node): node is ts.BinaryExpression =>
  ts.isBinaryExpression(node) &&
  node.operatorToken.kind === ts.SyntaxKind.PlusToken;

const readSourceWords = (): SourceWords => {
  const words: SourceWords = {
    whole: new Set(),
    prefixes: new Set(),
    suffixes: new Set(),
    literals: new Set(),
  };
  for (const file of sourceFiles(SRC)) {
    const source = ts.createSourceFile(
      file,
      readFileSync(file, 'utf8'),
      ts.ScriptTarget.Latest,
      true,
      file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS
    );
    const visit = (node: ts.Node): void => {
      if (
        ts.isStringLiteral(node) ||
        ts.isNoSubstitutionTemplateLiteral(node)
      ) {
        words.literals.add(node.text);
      }
      if (
        ts.isJsxAttribute(node) &&
        NON_CLASS_ATTRIBUTES.test(node.name.getText(source))
      ) {
        return;
      }
      const composite = ts.isTemplateExpression(node) || isPlus(node);
      if (composite && !(node.parent && isPlus(node.parent))) {
        addComposite(partsOf(node as ts.Expression), words);
      } else if (
        (ts.isStringLiteral(node) ||
          ts.isNoSubstitutionTemplateLiteral(node)) &&
        !(
          node.parent &&
          (isPlus(node.parent) || ts.isTemplateSpan(node.parent))
        )
      ) {
        addWords(node.text, words, false, false);
      }
      ts.forEachChild(node, visit);
    };
    visit(source);
  }
  return words;
};

const WORDS = readSourceWords();
const THEME_CSS = readFileSync(THEME_PATH, 'utf8');
const THEME = postcss.parse(THEME_CSS);
const CLASS_PREFIXES = [...WORDS.prefixes].filter(
  (prefix) => prefix.length >= 3 && !(prefix in NOT_CLASS_FRAGMENTS)
);
const CLASS_SUFFIXES = [...WORDS.suffixes].filter(
  (suffix) => suffix.length >= 3
);

const producible = (className: string): boolean =>
  WORDS.whole.has(className) ||
  CLASS_PREFIXES.some((prefix) => className.startsWith(prefix)) ||
  CLASS_SUFFIXES.some((suffix) => className.endsWith(suffix));

/** Other files that read the stylesheet's custom properties: Tailwind maps its colours to them. */
const OTHER_READERS = [path.resolve(process.cwd(), 'tailwind.config.js')].map(
  (file) => readFileSync(file, 'utf8')
);

/**
 * The classes an element must carry for the selector to match it. A class inside :not()
 * need not exist; of :is()/:where()/:has() alternatives one producible branch is enough.
 */
const requiredClasses = (selector: string): string[] => {
  let working = selector;
  for (let depth = 0; depth < 5; depth += 1) {
    working = working.replace(/:not\((?:[^()]|\([^()]*\))*\)/g, '');
  }
  working = working.replace(
    /:(is|where|has)\(((?:[^()]|\([^()]*\))*)\)/g,
    (_match, _kind: string, inner: string) => {
      const branches = inner.split(',');
      const live = branches.find((branch) =>
        [...branch.matchAll(CLASS)].every((match) => producible(match[1]))
      );
      return ` ${live ?? branches[0]} `;
    }
  );
  working = working.replace(/\[[^\]]*\]/g, '');
  return [...working.matchAll(CLASS)].map((match) => match[1]);
};

const inKeyframes = (rule: Rule): boolean =>
  rule.parent?.type === 'atrule' &&
  /keyframes$/.test((rule.parent as postcss.AtRule).name);

describe('theme.css', () => {
  it('names only classes the source can produce', () => {
    const dead: string[] = [];
    THEME.walkRules((rule) => {
      if (inKeyframes(rule)) return;
      for (const selector of rule.selectors) {
        const missing = requiredClasses(selector).filter(
          (name) => !producible(name)
        );
        if (missing.length) {
          dead.push(
            `line ${rule.source?.start?.line}: ${selector.trim()} (no .${missing.join(', .')})`
          );
        }
      }
    });
    expect(
      dead,
      'These selectors name classes that no component renders, so they never match. ' +
        'Remove them, or the rule if every selector in it is listed.'
    ).toEqual([]);
  });

  it('defines only custom properties that something reads', () => {
    const named = (text: string, prop: string): boolean =>
      new RegExp(`(^|[^\\w-])${prop}(?![\\w-])`).test(text);
    const readInCss = new Set(
      [...THEME_CSS.matchAll(/var\(\s*(--[\w-]+)/g)].map((match) => match[1])
    );
    const unread: string[] = [];
    THEME.walkDecls(/^--/, (decl) => {
      // d3 tracks read colours by name (cssVar('--color-cnv-clinical')).
      const readElsewhere =
        [...WORDS.literals].some((literal) => named(literal, decl.prop)) ||
        OTHER_READERS.some((text) => named(text, decl.prop));
      if (!readInCss.has(decl.prop) && !readElsewhere) unread.push(decl.prop);
    });
    expect(
      unread,
      'No stylesheet rule or source file reads these custom properties.'
    ).toEqual([]);
  });

  it('reads dynamic class names as patterns and everything else as literal words', () => {
    // `repeat-status--${status}` in the repeat-expansion page.
    expect(producible('repeat-status--pathogenic')).toBe(true);
    // `gene${n === 1 ? '' : 's'}` is two words, not a prefix that would excuse `.gene-*`.
    expect(WORDS.prefixes.has('gene')).toBe(false);
    expect(producible('zz-no-such-class')).toBe(false);
  });

  it('keeps its list of fragments that are not classes current', () => {
    const stale = Object.keys(NOT_CLASS_FRAGMENTS).filter(
      (fragment) => !WORDS.prefixes.has(fragment)
    );
    expect(
      stale,
      'These NOT_CLASS_FRAGMENTS entries are no longer produced; remove them.'
    ).toEqual([]);
  });
});
