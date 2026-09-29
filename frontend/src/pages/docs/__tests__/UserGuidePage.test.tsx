import { render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';

import UserGuidePage from '../UserGuidePage';

describe('UserGuidePage', () => {
  it('renders an anchored contents table and workspace quick links', () => {
    render(
      <MemoryRouter>
        <UserGuidePage />
      </MemoryRouter>
    );

    expect(screen.getByRole('heading', { name: /coga user guide/i })).toBeInTheDocument();
    expect(screen.getByRole('navigation', { name: /user guide contents/i })).toBeInTheDocument();

    const contentsNav = screen.getByRole('navigation', { name: /user guide contents/i });
    const tocLink = within(contentsNav).getByText('Quick start').closest('a');
    expect(tocLink).not.toBeNull();
    expect(tocLink).toHaveAttribute('href', '#quick-start');

    expect(screen.getByRole('link', { name: /dashboard start here/i })).toHaveAttribute(
      'href',
      '/dashboard'
    );
    expect(
      screen.getByRole('heading', { name: /small-variant prioritisation/i })
    ).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: /^administration$/i })).toBeInTheDocument();
    const variantExplorerLinks = screen.getAllByRole('link', {
      name: /variant explorer cross-cohort/i,
    });
    expect(variantExplorerLinks.length).toBeGreaterThan(0);
    expect(variantExplorerLinks[0]).toHaveAttribute('href', '/variant-explorer');
  });

  // #528: the sections render from Markdown. The content test holds the words; these
  // hold the pieces the Markdown conventions stand for.
  it('renders the Markdown conventions as the guide’s own blocks', () => {
    const { container } = render(
      <MemoryRouter>
        <UserGuidePage />
      </MemoryRouter>
    );

    // The one external link opens in a new tab; routes go through the router.
    expect(screen.getByRole('link', { name: 'Monarch Initiative' })).toHaveAttribute('target', '_blank');
    expect(screen.getByRole('link', { name: 'Monarch Initiative' })).toHaveAttribute('rel', 'noreferrer');

    // A "further-reading" link is the In-depth reference block.
    const further = container.querySelector('#sample-qc .user-guide-further-reading');
    expect(further?.querySelector('.user-guide-further-reading-label')).toHaveTextContent('In-depth reference');
    expect(further?.querySelector('a.user-guide-doc-link')).toHaveAttribute('href', '/docs/reference/sample-qc');
    expect(further?.querySelector('a')?.getAttribute('title')).toBeNull();

    // A blockquote is a callout, a cards fence a card grid, a table sits in its wrapper.
    expect(container.querySelectorAll('.user-guide-section-prose blockquote')).toHaveLength(0);
    expect(container.querySelectorAll('.user-guide-callout')).toHaveLength(22);
    expect(container.querySelectorAll('.user-guide-mini-grid > .user-guide-mini-card')).toHaveLength(6);
    expect(container.querySelector('.user-guide-section-prose pre')).toBeNull();
    expect(container.querySelectorAll('.user-guide-section-prose .content-table-wrap > table')).toHaveLength(6);
  });
});
