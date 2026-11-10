import { lazy } from 'react';

// Each page of the app is a file of its own, loaded when it is first opened, and named by a
// hash of its content; a release replaces those files with new names. A tab opened before a
// release still asks for the previous release's names, which are gone (server.mjs answers 404
// for them), so the page it opens cannot load. That page is then loaded by reloading the tab once, which
// fetches the release's own files. Opening a page has already left the one before, so the
// reload loses nothing on screen.

const RELOADED_AT_KEY = 'coga:reloaded-for-release-at';
// One reload a minute at most: a page that still fails after it is shown as an error.
const RELOAD_GUARD_MS = 60_000;

/** The tab's reload, as an object a test can spy on (jsdom's location cannot be changed). */
export const tab = { reload: () => window.location.reload() };

/**
 * Reload the tab because a part of the app could not be loaded, unless it was reloaded for that
 * in the last minute, the browser is offline, or the session's storage cannot record the reload
 * (without that record, a page that keeps failing would reload forever). Whether it reloads.
 */
export function reloadForNewRelease(now: number = Date.now()): boolean {
  if (navigator.onLine === false) return false;
  try {
    const last = Number(sessionStorage.getItem(RELOADED_AT_KEY));
    if (last > 0 && now >= last && now - last < RELOAD_GUARD_MS) return false;
    sessionStorage.setItem(RELOADED_AT_KEY, String(now));
  } catch {
    return false;
  }
  tab.reload();
  return true;
}

/**
 * React's `lazy` for a page opened by its route: a page that cannot be loaded reloads the tab
 * once (`reloadForNewRelease`), showing the loading state until it does; otherwise its error
 * reaches the error screen. Not for a part shown over a page, such as a dialog: a reload there
 * would lose what the page beneath holds.
 */
export const lazyPage: typeof lazy = (load) =>
  lazy(() =>
    load().catch((error: unknown) => {
      if (reloadForNewRelease()) return new Promise<never>(() => {});
      throw error;
    }),
  );

/**
 * Whether `error` is a part of the app that could not be loaded, as each browser words it
 * (Chrome, Firefox, Safari) and as Vite words a stylesheet it could not load with it.
 */
export function isModuleLoadError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error);
  return /dynamically imported module|Importing a module script failed|Unable to preload CSS/i.test(
    message,
  );
}
