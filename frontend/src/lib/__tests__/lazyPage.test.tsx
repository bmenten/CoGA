import { render, screen } from '@testing-library/react';
import { Suspense } from 'react';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import ErrorBoundary from '../../components/ErrorBoundary';
import { isModuleLoadError, lazyPage, reloadForNewRelease, tab } from '../lazyPage';

// What Chrome says when a page file a release replaced cannot be loaded.
const STALE = new TypeError(
  'Failed to fetch dynamically imported module: https://coga.example/assets/SomePage-OLDHASH.js',
);

const page = (load: () => Promise<{ default: React.ComponentType }>) => {
  const Page = lazyPage(load);
  return (
    <MemoryRouter>
      <ErrorBoundary>
        <Suspense fallback={<p>Loading page</p>}>
          <Page />
        </Suspense>
      </ErrorBoundary>
    </MemoryRouter>
  );
};

describe('reloadForNewRelease', () => {
  let reload: ReturnType<typeof vi.spyOn>;
  beforeEach(() => {
    reload = vi.spyOn(tab, 'reload').mockImplementation(() => undefined);
  });
  afterEach(() => vi.restoreAllMocks());

  it('reloads the tab once a minute at most', () => {
    expect(reloadForNewRelease(1_000_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(1);
    // A page that still fails after the reload: no second one.
    expect(reloadForNewRelease(1_030_000)).toBe(false);
    expect(reload).toHaveBeenCalledTimes(1);
    // A later release: reloaded again.
    expect(reloadForNewRelease(1_061_000)).toBe(true);
    expect(reload).toHaveBeenCalledTimes(2);
  });

  it('does not reload while offline', () => {
    vi.spyOn(navigator, 'onLine', 'get').mockReturnValue(false);
    expect(reloadForNewRelease(2_000_000)).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });

  it('does not reload when it cannot record the reload', () => {
    vi.stubGlobal('sessionStorage', {
      getItem: () => {
        throw new DOMException('denied', 'SecurityError');
      },
      setItem: () => undefined,
    });
    try {
      expect(reloadForNewRelease(3_000_000)).toBe(false);
      expect(reload).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
});

describe('lazyPage', () => {
  afterEach(() => vi.restoreAllMocks());

  it('reloads the tab when the page cannot be loaded, showing the loading state meanwhile', async () => {
    const reload = vi.spyOn(tab, 'reload').mockImplementation(() => undefined);
    render(page(() => Promise.reject(STALE)));
    await vi.waitFor(() => expect(reload).toHaveBeenCalledTimes(1));
    expect(screen.getByText('Loading page')).toBeInTheDocument();
    expect(screen.queryByText('This view could not be loaded')).not.toBeInTheDocument();
  });

  it('shows the error screen, offering a reload, when the page still fails after one', async () => {
    const reload = vi.spyOn(tab, 'reload').mockImplementation(() => undefined);
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    sessionStorage.setItem('coga:reloaded-for-release-at', String(Date.now()));
    render(page(() => Promise.reject(STALE)));
    expect(await screen.findByText('This view could not be loaded')).toBeInTheDocument();
    expect(reload).not.toHaveBeenCalled();
    // "Try again" would fail the same way: React keeps the failed load.
    expect(screen.queryByRole('button', { name: 'Try again' })).not.toBeInTheDocument();
    screen.getByRole('button', { name: 'Reload page' }).click();
    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('renders the page it loads', async () => {
    render(page(() => Promise.resolve({ default: () => <p>The page</p> })));
    expect(await screen.findByText('The page')).toBeInTheDocument();
  });
});

describe('isModuleLoadError', () => {
  it('knows each browser’s wording, and Vite’s for a stylesheet', () => {
    expect(isModuleLoadError(STALE)).toBe(true);
    expect(isModuleLoadError(new TypeError('error loading dynamically imported module: https://x/a.js'))).toBe(true);
    expect(isModuleLoadError(new TypeError('Importing a module script failed.'))).toBe(true);
    expect(isModuleLoadError(new Error('Unable to preload CSS for /assets/a.css'))).toBe(true);
    expect(isModuleLoadError(new Error('render exploded'))).toBe(false);
  });
});
