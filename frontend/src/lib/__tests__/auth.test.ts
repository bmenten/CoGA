import { beforeEach, describe, expect, it } from 'vitest';
import {
  clearSession,
  getAuthToken,
  getStoredRole,
  getStoredUsername,
  isAdmin,
  isAuthenticated,
  persistSession,
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
});
