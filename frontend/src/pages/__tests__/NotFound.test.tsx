// Pins the catch-all page: it names the unmatched path (as text, never markup), links to the
// dashboard, and "Go back" steps back one entry in the browser history.
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, describe, expect, it, vi } from 'vitest';

import NotFound from '../NotFound';

const renderAt = (path: string) =>
  render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/dashboard" element={<p>Dashboard landing</p>} />
        <Route path="*" element={<NotFound />} />
      </Routes>
    </MemoryRouter>,
  );

describe('NotFound', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('says which path matched no route', () => {
    renderAt('/families/F1/no-such-view');

    expect(screen.getByRole('heading', { name: 'Page not found' })).toBeInTheDocument();
    expect(screen.getByText('No route matches /families/F1/no-such-view.')).toBeInTheDocument();
  });

  it('shows a crafted path as text, never as markup', () => {
    const { container } = renderAt('/<img src=x onerror=alert(1)>');

    expect(screen.getByText('No route matches /<img src=x onerror=alert(1)>.')).toBeInTheDocument();
    expect(container.querySelector('img')).toBeNull();
  });

  it('links to the dashboard', async () => {
    const user = userEvent.setup();
    renderAt('/nowhere');

    const link = screen.getByRole('link', { name: 'Go to Dashboard' });
    expect(link).toHaveAttribute('href', '/dashboard');

    await user.click(link);
    expect(screen.getByText('Dashboard landing')).toBeInTheDocument();
  });

  it('"Go back" steps back one entry in the browser history', async () => {
    const user = userEvent.setup();
    const back = vi.spyOn(window.history, 'back').mockImplementation(() => undefined);
    renderAt('/nowhere');

    await user.click(screen.getByRole('button', { name: 'Go back' }));

    expect(back).toHaveBeenCalledTimes(1);
  });
});
