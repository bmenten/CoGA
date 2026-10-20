import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearSession,
  getAuthToken,
  getSessionSignIn,
  getStoredRole,
  getStoredUsername,
  isAdmin,
  isAuthenticated,
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
