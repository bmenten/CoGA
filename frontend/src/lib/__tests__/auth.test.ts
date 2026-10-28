import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import { inAnotherTab } from '../../test/otherTab';
import {
  clearSession,
  getAuthToken,
  getSessionSignIn,
  getStoredRole,
  getStoredUsername,
  isAdmin,
  isAuthenticated,
  onSessionEndedElsewhere,
  persistSession,
  updateSessionToken,
} from '../auth';

describe('auth storage helpers', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('persists and reads a session', () => {
    persistSession('token-123', 'user@example.com', 'admin');

    expect(getAuthToken()).toBe('token-123');
    expect(getStoredUsername()).toBe('user@example.com');
    expect(getStoredRole()).toBe('admin');
    expect(isAuthenticated()).toBe(true);
    expect(isAdmin()).toBe(true);
  });

  it('clears session state and ignores invalid roles', () => {
    persistSession('token-123', 'user@example.com', 'operator');

    expect(getStoredRole()).toBeNull();
    expect(isAdmin()).toBe(false);

    clearSession();

    expect(getAuthToken()).toBeNull();
    expect(getStoredUsername()).toBeNull();
    expect(getStoredRole()).toBeNull();
    expect(isAuthenticated()).toBe(false);
  });

  // SEC-4: a session says how it signed in, so the API client knows to renew a Microsoft token.
  it('keeps how the session signed in: with a password unless it says Microsoft', () => {
    const first = ['first', 'access'].join('-');
    persistSession(first, 'user@example.com', 'viewer');
    expect(getSessionSignIn()).toBe('password');

    persistSession(first, 'user@example.com', 'viewer', 'microsoft');
    expect(getSessionSignIn()).toBe('microsoft');

    clearSession();
    expect(getSessionSignIn()).toBe('password');
    expect(localStorage.getItem('sign_in')).toBeNull();
  });

  it('keeps a renewed token for a session, and makes no session of one', () => {
    const [first, renewed] = ['first', 'renewed'].map((part) => [part, 'access'].join('-'));
    updateSessionToken(renewed);
    expect(getAuthToken()).toBeNull();

    persistSession(first, 'user@example.com', 'viewer', 'microsoft');
    updateSessionToken(renewed);
    expect(getAuthToken()).toBe(renewed);
    expect(getStoredUsername()).toBe('user@example.com');
  });
});

// The tabs of a browser share the session: it is kept in the browser's storage. A tab that had
// written the session itself (it signed in, or renewed a Microsoft token) read back its own copy
// once another tab had logged out, and its next renewal wrote the token back for every tab (found
// while fixing SEC-36).
describe('a session shared by the tabs of a browser', () => {
  // Another tab: the same browser storage, and a page of its own.
  const otherTab = async () => {
    vi.resetModules();
    return import('../auth');
  };

  beforeEach(() => {
    clearSession();
  });

  it('ends in this tab when another tab logs out, though this tab signed in', async () => {
    const token = ['session', 'access'].join('-');
    persistSession(token, 'user@example.com', 'admin', 'microsoft');
    const tabB = await otherTab();
    expect(tabB.getAuthToken()).toBe(token);

    tabB.clearSession();

    expect(getAuthToken()).toBeNull();
    expect(isAuthenticated()).toBe(false);
    expect(getStoredUsername()).toBeNull();
    expect(getStoredRole()).toBeNull();
    expect(getSessionSignIn()).toBe('password');
  });

  it('is not brought back by a renewal this tab receives after another tab logged out', async () => {
    const [first, renewed, late] = ['first', 'renewed', 'late'].map((part) => [part, 'access'].join('-'));
    persistSession(first, 'user@example.com', 'viewer', 'microsoft');
    updateSessionToken(renewed);
    const tabB = await otherTab();

    tabB.clearSession();
    // MSAL hands back a token to a request this tab sent before the logout.
    updateSessionToken(late);

    // Before, the renewal signed every tab in again, the one that logged out included.
    expect(tabB.getAuthToken()).toBeNull();
    expect(getAuthToken()).toBeNull();
  });

  it('tells this tab when another tab ends it, not when it renews the token', () => {
    const token = ['session', 'access'].join('-');
    persistSession(token, 'user@example.com', 'viewer', 'microsoft');
    const onEnded = vi.fn();
    const stop = onSessionEndedElsewhere(onEnded);

    // The browser tells a tab of each change another tab makes to its storage.
    window.dispatchEvent(new StorageEvent('storage', { key: 'token', oldValue: token, newValue: 'renewed' }));
    window.dispatchEvent(new StorageEvent('storage', { key: 'username', oldValue: 'user@example.com', newValue: null }));
    expect(onEnded).not.toHaveBeenCalled();

    window.dispatchEvent(new StorageEvent('storage', { key: 'token', oldValue: token, newValue: null }));
    expect(onEnded).toHaveBeenCalledTimes(1);
    // Another tab cleared the storage.
    window.dispatchEvent(new StorageEvent('storage', { key: null }));
    expect(onEnded).toHaveBeenCalledTimes(2);

    stop();
    window.dispatchEvent(new StorageEvent('storage', { key: 'token', oldValue: token, newValue: null }));
    expect(onEnded).toHaveBeenCalledTimes(2);
  });

  // A sign-in of another account in another tab (on a sign-in page left open there from before
  // this tab signed in) replaced the session this tab shows without a logout, and this tab's
  // requests went out as that account.
  describe('signed into in another tab', () => {
    const [first, renewed, next] = ['first', 'renewed', 'next'].map((part) => [part, 'access'].join('-'));
    let onEnded: Mock<() => void>;
    let stop: () => void;

    beforeEach(() => {
      persistSession(first, 'analyst@example.org', 'viewer', 'microsoft');
      onEnded = vi.fn<() => void>();
      stop = onSessionEndedElsewhere(onEnded);
    });

    afterEach(() => {
      stop();
    });

    it('as another account, ends in this tab', async () => {
      const tabB = await otherTab();

      inAnotherTab(() => tabB.persistSession(next, 'colleague@example.org', 'viewer', 'microsoft'));

      expect(onEnded).toHaveBeenCalledTimes(1);
    });

    it('as the same account, goes on: the token is replaced, the account stays', async () => {
      const tabB = await otherTab();

      // An administrator changed the account's role since this tab signed in.
      inAnotherTab(() => tabB.persistSession(next, 'analyst@example.org', 'admin', 'microsoft'));

      expect(onEnded).not.toHaveBeenCalled();
    });

    it('goes on when the other tab renews the token, and ends when it logs out', async () => {
      const tabB = await otherTab();

      inAnotherTab(() => tabB.updateSessionToken(renewed));
      expect(onEnded).not.toHaveBeenCalled();

      inAnotherTab(() => tabB.clearSession());
      expect(onEnded).toHaveBeenCalledTimes(1);
    });
  });
});
