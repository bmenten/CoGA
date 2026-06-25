import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
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

  it('shows a not-found state for an unknown slug', () => {
    renderAt('/docs/reference/does-not-exist');
    expect(screen.getByText(/Reference document not found/i)).toBeInTheDocument();
  });

  // Every registered reference doc renders without error and exposes its title heading.
  it.each(referenceDocs.map((doc) => [doc.slug, doc.title] as const))(
    'renders the "%s" reference doc with an h1 ending in "— reference"',
    (slug) => {
      renderAt(`/docs/reference/${slug}`);
      const heading = document.querySelector('h1');
      expect(heading).toBeTruthy();
      expect(heading?.textContent || '').toMatch(/— reference$/);
    },
  );
});
