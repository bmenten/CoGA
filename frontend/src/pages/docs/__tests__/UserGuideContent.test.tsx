// The user guide's content (#528): the guide moved from JSX to Markdown
// (src/content/docs/user-guide/*.md). It is part of the information for safety (TF-15),
// so the move must not change a word. This test compares each section, as rendered,
// against a snapshot taken from the JSX guide before the move
// (fixtures/user-guide-content.json): the section's whole text, its paragraphs,
// headings, list items, table cells, callouts, cards and further-reading links, and every
// emphasis, code span and link with its target.
//
// Regenerate only for an intended content change: COGA_REGENERATE_GOLDEN=1 npx vitest run
// src/pages/docs/__tests__/UserGuideContent.test.tsx
import { render } from '@testing-library/react';
import { readFileSync, writeFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { MemoryRouter } from 'react-router';
import { expect, test } from 'vitest';

import UserGuidePage from '../UserGuidePage';

// Vitest runs from frontend/.
const FIXTURE = resolve(process.cwd(), 'src/pages/docs/__tests__/fixtures/user-guide-content.json');

const collapse = (value: string | null | undefined) => (value ?? '').replace(/\s+/g, ' ').trim();

const BLOCKS = [
  'h3',
  'li',
  'th',
  'td',
  '.user-guide-callout',
  '.user-guide-mini-card-title',
  '.user-guide-mini-card-copy',
].join(', ');

const describeSection = (section: Element) => {
  const prose = section.querySelector('.user-guide-section-prose');
  if (!prose) throw new Error(`section ${section.id} has no prose`);
  const header = section.querySelector('.user-guide-section-header');
  return {
    title: collapse(header?.querySelector('h2')?.textContent),
    summary: collapse(header?.querySelector('.user-guide-section-summary')?.textContent),
    quickLinks: Array.from(section.querySelectorAll('.user-guide-link-chip')).map((link) => [
      link.getAttribute('aria-label'),
      link.getAttribute('href'),
    ]),
    // Every character of the section, whitespace aside: blocks are separated by newlines
    // in the Markdown rendering and by nothing in JSX, which is not content. The lists
    // below keep the spacing within each block.
    text: (prose.textContent ?? '').replace(/\s+/g, ''),
    // Paragraphs directly in the section (a callout's own paragraph is counted with it).
    paragraphs: Array.from(prose.children)
      .filter((child) => child.tagName === 'P' && !child.classList.contains('user-guide-further-reading'))
      .map((p) => collapse(p.textContent)),
    blocks: Array.from(prose.querySelectorAll(BLOCKS)).map((block) => [
      block.matches('.user-guide-callout')
        ? 'callout'
        : block.matches('.user-guide-mini-card-title')
          ? 'card-title'
          : block.matches('.user-guide-mini-card-copy')
            ? 'card-copy'
            : block.tagName.toLowerCase(),
      collapse(block.textContent),
    ]),
    inline: Array.from(prose.querySelectorAll('strong, em, code, a')).map((element) =>
      element.tagName === 'A'
        ? ['a', collapse(element.textContent), element.getAttribute('href')]
        : [element.tagName.toLowerCase(), collapse(element.textContent)],
    ),
    furtherReading: Array.from(prose.querySelectorAll('.user-guide-further-reading')).map((p) => [
      collapse(p.textContent),
      p.querySelector('a')?.getAttribute('href'),
    ]),
    tables: prose.querySelectorAll('.content-table-wrap > table').length,
    cardGrids: prose.querySelectorAll('.user-guide-mini-grid').length,
  };
};

test('every section of the guide says what it said before the move to Markdown', () => {
  const { container } = render(
    <MemoryRouter>
      <UserGuidePage />
    </MemoryRouter>,
  );
  const sections = Array.from(container.querySelectorAll('section.user-guide-section'));
  const observed = Object.fromEntries(sections.map((section) => [section.id, describeSection(section)]));

  if (process.env.COGA_REGENERATE_GOLDEN === '1') {
    writeFileSync(FIXTURE, `${JSON.stringify(observed, null, 1)}\n`);
    if (process.env.COGA_GUIDE_HTML_DIR) {
      sections.forEach((section) =>
        writeFileSync(`${process.env.COGA_GUIDE_HTML_DIR}/${section.id}.html`, section.querySelector('.user-guide-section-prose')!.innerHTML),
      );
    }
    return;
  }

  const expected = JSON.parse(readFileSync(FIXTURE, 'utf8'));
  expect(Object.keys(observed)).toEqual(Object.keys(expected));
  for (const id of Object.keys(expected)) {
    expect({ id, ...observed[id] }).toEqual({ id, ...expected[id] });
  }
});
