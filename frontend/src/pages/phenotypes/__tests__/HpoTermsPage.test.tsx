// Pins the HPO term list opened from an SV's annotations: terms split on ; , and |, each shown
// once in sorted order, linked out to the HPO browser as one encoded segment, and the way back.
import { render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it } from 'vitest';

import HpoTermsPage from '../HpoTermsPage';

const renderAt = (url: string) =>
  render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/hpo" element={<HpoTermsPage />} />
      </Routes>
    </MemoryRouter>,
  );

const termHrefs = () =>
  screen.getAllByRole('link', { name: /open hpo/i }).map((link) => link.getAttribute('href'));

describe('HpoTermsPage', () => {
  it('shows every distinct term once, whichever separator the annotation used, in sorted order', () => {
    // AnnotSV-style fields arrive as one delimited string or as repeated params, with
    // stray whitespace and empty entries; a term listed twice is still one term.
    renderAt(
      `/hpo?term=${encodeURIComponent('HP:0001250;HP:0000707')}` +
        `&term=${encodeURIComponent('HP:0001250|HP:0004322')}` +
        `&term=${encodeURIComponent(' HP:0000707 ,,')}`,
    );

    expect(
      screen.getByText('3 HPO terms selected from structural variant annotations.'),
    ).toBeInTheDocument();
    expect(termHrefs()).toEqual([
      'https://hpo.jax.org/browse/term/HP%3A0000707',
      'https://hpo.jax.org/browse/term/HP%3A0001250',
      'https://hpo.jax.org/browse/term/HP%3A0004322',
    ]);
    expect(screen.getByText('HP:0000707')).toBeInTheDocument();
  });

  it('opens the HPO browser in a new tab without handing it this window', () => {
    renderAt('/hpo?term=HP:0001250');

    const link = screen.getByRole('link', { name: /open hpo/i });
    expect(link).toHaveAttribute('target', '_blank');
    expect(link).toHaveAttribute('rel', 'noreferrer');
    expect(screen.getByText('1 HPO term selected from structural variant annotations.')).toBeInTheDocument();
  });

  it('encodes a term as a single path segment, so annotation text cannot redirect the link', () => {
    renderAt(`/hpo?term=${encodeURIComponent('../search?q=x#top')}`);

    expect(termHrefs()).toEqual(['https://hpo.jax.org/browse/term/..%2Fsearch%3Fq%3Dx%23top']);
  });

  it('says so when no terms were passed, or only separators', () => {
    const { unmount } = renderAt('/hpo');

    expect(screen.getByText('No HPO terms were provided.')).toBeInTheDocument();
    expect(screen.getByText('No HPO terms to display.')).toBeInTheDocument();
    expect(screen.queryAllByRole('link')).toHaveLength(0);
    unmount();

    renderAt(`/hpo?term=${encodeURIComponent(' ; , | ')}&term=`);
    expect(screen.getByText('No HPO terms were provided.')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /open hpo/i })).not.toBeInTheDocument();
  });

  it("links back to the family's structural variants only when the family is known", () => {
    const { unmount } = renderAt('/hpo?term=HP:0001250&family_id=F1');

    expect(screen.getByRole('link', { name: 'Back to SVs' })).toHaveAttribute(
      'href',
      '/families/F1/structural-variants',
    );
    unmount();

    renderAt('/hpo?term=HP:0001250');
    expect(screen.queryByRole('link', { name: 'Back to SVs' })).not.toBeInTheDocument();
  });
});
