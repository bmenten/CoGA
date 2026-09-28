import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import ErrorBoundary from '../ErrorBoundary';

const Boom = () => {
  throw new Error('render exploded');
};

const tree = (resetKey: string, child: React.ReactNode) => (
  <MemoryRouter>
    <ErrorBoundary resetKey={resetKey}>{child}</ErrorBoundary>
  </MemoryRouter>
);

describe('ErrorBoundary', () => {
  afterEach(() => vi.restoreAllMocks());

  it('clears the error screen when the route changes (#510)', () => {
    vi.spyOn(console, 'error').mockImplementation(() => {});
    const { rerender } = render(tree('/families/F1', <Boom />));
    expect(screen.getByText('This view could not be displayed')).toBeInTheDocument();

    // Same route: the error stays.
    rerender(tree('/families/F1', <p>next view</p>));
    expect(screen.getByText('This view could not be displayed')).toBeInTheDocument();

    // Navigating away (e.g. the error screen's own "Dashboard" link) shows the new view.
    rerender(tree('/dashboard', <p>next view</p>));
    expect(screen.queryByText('This view could not be displayed')).not.toBeInTheDocument();
    expect(screen.getByText('next view')).toBeInTheDocument();
  });
});
