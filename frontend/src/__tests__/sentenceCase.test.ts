// The app writes its headings and kickers in sentence case since #754: the first word
// capitalised, and after it only names and abbreviations. Some were left in title case
// ("Gene Explorer", "Package Import", "Selected Family"; ENG-10). This scans the app's source,
// tests left out, for a heading or a kicker with a capital the rule does not allow.

import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { describe, expect, it } from 'vitest';

const sourceRoot = path.resolve(process.cwd(), 'src');
const sources = (dir: string): string[] =>
  readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) return entry.name === '__tests__' || entry.name === 'test' ? [] : sources(full);
    return entry.name.endsWith('.tsx') ? [full] : [];
  });

/** Names a heading may hold after its first word: tools, sources, standards and the like. */
const NAMES = new Set([
  'ACMG', 'APCAD', 'BED', 'CNV', 'CNVs', 'ClickHouse', 'ClinGen', 'ClinVar', 'CoGA', 'DNA', 'GenCC', 'HPO',
  'IGV', 'Mendelian', 'Monarch', 'NIPT', 'Needlr', 'NeedlR', 'PED', 'PGT', 'QC', 'ROI', 'SF', 'SNV', 'SV', 'SVs',
  'TRGT', 'VCF', 'Paraphase', 'Circos', 'MANE', 'Microsoft', 'PanelApp', 'Mb', 'X', 'Y', 'MT',
]);

/** Text a heading or a kicker shows: `className="page-kicker">…<`, `<h2 …>…<`, `kicker="…"`. */
const HEADING = /<(?:h[1-4])[^>]*>\s*([^<>{}]+?)\s*</g;
const KICKER = /className="page-kicker">\s*([^<>{}]+?)\s*</g;
const KICKER_PROP = /\bkicker="([^"]+)"/g;

const titleCaseWords = (text: string): string[] =>
  (text.match(/[A-Za-z][A-Za-z0-9’'-]*/g) ?? [])
    .slice(1)
    .filter((word) => /^[A-Z][a-z]/.test(word) && !NAMES.has(word));

const headings = (text: string): string[] =>
  [HEADING, KICKER, KICKER_PROP].flatMap((pattern) => [...text.matchAll(pattern)].map((match) => match[1]));

describe('headings and kickers', () => {
  it('are written in sentence case', () => {
    const found = sources(sourceRoot).flatMap((file) =>
      headings(readFileSync(file, 'utf8'))
        .filter((heading) => titleCaseWords(heading).length > 0)
        .map((heading) => `${path.relative(sourceRoot, file)}: ${heading}`),
    );
    expect(found).toEqual([]);
  });

  it('would find one in title case, and allows names', () => {
    expect(headings('<h1 className="catalog-card-title">Gene Explorer</h1>').map(titleCaseWords)).toEqual([['Explorer']]);
    expect(headings('<p className="page-kicker">Selected Family</p>').map(titleCaseWords)).toEqual([['Family']]);
    expect(headings('<PageState kicker="Small Variants" />').map(titleCaseWords)).toEqual([['Variants']]);
    expect(titleCaseWords('Clinical CNV explorer')).toEqual([]);
    expect(titleCaseWords('Raw data provenance')).toEqual([]);
  });
});
