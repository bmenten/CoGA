// The ids the in-app reference docs give their headings (ENG-19): the slugs GitHub gives the
// same Markdown, which the docs' links to their own headings are written against.
import { describe, expect, it } from 'vitest';

import { createHeadingSlugger, rehypeHeadingIds } from '../headingIds';

const slugs = (texts: string[]) => texts.map(createHeadingSlugger());

describe('createHeadingSlugger', () => {
  it('lowercases, drops punctuation and symbols, and turns each space into a hyphen', () => {
    expect(
      slugs([
        'Haplotype segregation analysis — reference',
        'The embryo call (at the ROI)',
        'Sample QC: a man’s X (PAR1/PAR2)',
        'snake_case & 50% — täg',
      ]),
    ).toEqual([
      'haplotype-segregation-analysis--reference',
      'the-embryo-call-at-the-roi',
      'sample-qc-a-mans-x-par1par2',
      'snake_case--50--täg',
    ]);
  });

  it('numbers a repeated slug from 1, skipping one already taken', () => {
    expect(slugs(['Notes', 'Notes', 'Notes-1', 'Notes'])).toEqual(['notes', 'notes-1', 'notes-1-1', 'notes-2']);
  });

  it('starts afresh for each document', () => {
    expect(slugs(['Notes'])).toEqual(['notes']);
    expect(slugs(['Notes'])).toEqual(['notes']);
  });
});

describe('rehypeHeadingIds', () => {
  type Node = {
    type: string;
    tagName?: string;
    value?: string;
    properties?: Record<string, unknown>;
    children?: Node[];
  };
  const text = (value: string): Node => ({ type: 'text', value });
  const element = (tagName: string, children: Node[], properties: Record<string, unknown> = {}): Node => ({
    type: 'element',
    tagName,
    properties,
    children,
  });

  it('gives each heading its slug from all of its text, and leaves other elements and set ids be', () => {
    const tree: Node = {
      type: 'root',
      children: [
        element('h2', [text('The '), element('strong', [text('two')]), text(' layers')]),
        element('p', [text('The two layers')]),
        element('h3', [text('Kept')], { id: 'own-id' }),
        element('blockquote', [element('h4', [text('The two layers')])]),
      ],
    };

    rehypeHeadingIds()(tree);

    const [h2, p, h3, quote] = tree.children ?? [];
    expect(h2.properties?.id).toBe('the-two-layers');
    expect(p.properties?.id).toBeUndefined();
    expect(h3.properties?.id).toBe('own-id');
    expect(quote.children?.[0].properties?.id).toBe('the-two-layers-1');
  });
});
