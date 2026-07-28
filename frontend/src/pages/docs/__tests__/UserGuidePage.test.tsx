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
});
