import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import ReferenceDocPage from '../ReferenceDocPage';
import { referenceDocs } from '../referenceDocs';

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/docs/reference/:slug" element={<ReferenceDocPage />} />
      </Routes>
    </MemoryRouter>,
  );

describe('ReferenceDocPage', () => {
  it('renders the bundled Sample QC markdown (headings, table content)', () => {
    renderAt('/docs/reference/sample-qc');
    expect(
      screen.getByRole('heading', { name: /Sample-integrity QC — reference/i }),
    ).toBeInTheDocument();
    // GFM content from the markdown renders.
    expect(screen.getByText(/application-aware/i)).toBeInTheDocument();
    expect(screen.getByText(/KING-robust kinship/i)).toBeInTheDocument();
    // Tables are rendered (wrapped for horizontal scroll).
    expect(document.querySelector('.content-table-wrap table')).toBeTruthy();
  });

  it('heads the page with the document title, as every page is headed, above the document', () => {
    renderAt('/docs/reference/sample-qc');
    const title = screen.getByRole('heading', { level: 1, name: 'Sample-integrity QC — reference' });
    expect(title.closest('.page-top-card')).not.toBeNull();
    expect(title).toHaveAttribute('id', 'sample-integrity-qc--reference');
    expect(screen.getAllByRole('heading', { level: 1 })).toHaveLength(1);
    expect(document.querySelector('article h1')).toBeNull();
    expect(screen.getByRole('link', { name: '← User guide' })).toHaveAttribute('href', '/docs');
  });

  it('shows a not-found state for an unknown slug', () => {
    renderAt('/docs/reference/does-not-exist');
    expect(screen.getByText(/Reference document not found/i)).toBeInTheDocument();
  });

  // Every registered reference doc renders without error and exposes its title heading.
  it.each(referenceDocs.map((doc) => doc.slug))(
    'renders the "%s" reference doc with an h1 ending in "— reference"',
    (slug) => {
      renderAt(`/docs/reference/${slug}`);
      const heading = document.querySelector('h1');
      expect(heading).toBeTruthy();
      expect(heading?.textContent || '').toMatch(/— reference$/);
    },
  );

  // ENG-19: the headings had no ids, so a link to a heading of the same page did not jump.
  describe('heading ids', () => {
    const ids = () => Array.from(document.querySelectorAll('article [id]')).map((element) => element.id);

    it('gives each heading the id GitHub gives it, so the haplotype reference’s own links jump', () => {
      renderAt('/docs/reference/haplotype-segregation');

      expect(screen.getByRole('heading', { level: 1, name: /Haplotype segregation analysis/ })).toHaveAttribute(
        'id',
        'haplotype-segregation-analysis--reference',
      );
      expect(screen.getByRole('heading', { level: 2, name: 'The embryo call (at the ROI)' })).toHaveAttribute(
        'id',
        'the-embryo-call-at-the-roi',
      );
      expect(screen.getByRole('heading', { level: 3, name: 'The blocks on chrX' })).toHaveAttribute(
        'id',
        'the-blocks-on-chrx',
      );
      const toQc = screen.getAllByRole('link', { name: 'Phasing QC at the ROI' })[0];
      expect(toQc).toHaveAttribute('href', '#phasing-qc-at-the-roi');
      expect(document.getElementById('phasing-qc-at-the-roi')).toHaveTextContent('Phasing QC at the ROI');
    });

    it.each(referenceDocs.map((doc) => doc.slug))('leads each in-page link of "%s" to one heading', (slug) => {
      renderAt(`/docs/reference/${slug}`);

      const pageIds = ids();
      expect(new Set(pageIds).size).toBe(pageIds.length);
      const fragments = Array.from(document.querySelectorAll('article a[href^="#"]')).map((link) =>
        decodeURIComponent((link.getAttribute('href') ?? '').slice(1)),
      );
      fragments.forEach((fragment) => {
        const target = document.getElementById(fragment);
        expect(target, `#${fragment}`).not.toBeNull();
        expect(target?.tagName, `#${fragment}`).toMatch(/^H[1-6]$/);
      });
    });
  });
});
