import type { InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api, { endExpiredSession, hasAuthorizationHeader, normalizeApiTransportError, shouldAttachStoredToken } from '../api';
import { getAuthToken, persistSession } from '../auth';

describe('api auth header handling', () => {
  it('detects authorization headers regardless of casing', () => {
    expect(hasAuthorizationHeader({ Authorization: 'Bearer fresh-token' })).toBe(true);
    expect(hasAuthorizationHeader({ authorization: 'Bearer fresh-token' })).toBe(true);
    expect(hasAuthorizationHeader({ 'X-Test': 'value' })).toBe(false);
  });

  it('does not attach stored tokens to auth endpoints', () => {
    expect(shouldAttachStoredToken('/auth/login')).toBe(false);
    expect(shouldAttachStoredToken('/auth/signup')).toBe(false);
  });

  it('does not override an explicit authorization header', () => {
    expect(
      shouldAttachStoredToken('/auth/me', {
        Authorization: 'Bearer fresh-token',
      })
    ).toBe(false);
  });

  it('still attaches the stored token to normal API requests without headers', () => {
    expect(shouldAttachStoredToken('/families/F1')).toBe(true);
  });

  it('normalizes raw transport errors before callers inspect the message', () => {
    const error = Object.assign(new Error('Network Error'), { request: {} });

    normalizeApiTransportError(error, '/api');

    expect(error.message).toBe(
      'Unable to reach the API at /api. Check that the backend, Postgres, and ClickHouse services are running.'
    );
  });
});

describe('an expired session', () => {
  it('signs in again to the page the session expired on (UI-3)', () => {
    persistSession('session-token', 'analyst', 'viewer');
    const replace = vi.fn();
    endExpiredSession({ pathname: '/families/F1/small-variants', search: '?project_id=P1', hash: '#v2', replace });

    expect(getAuthToken()).toBeNull();
    // As RequireAuth writes it: the page's path, query string and hash, percent-encoded. Before,
    // the sign-in opened the dashboard.
    expect(replace).toHaveBeenCalledWith('/login?next=%2Ffamilies%2FF1%2Fsmall-variants%3Fproject_id%3DP1%23v2');
  });

  it('stays on a sign-in page, which shows the refusal itself', () => {
    const replace = vi.fn();
    for (const pathname of ['/login', '/login/emergency']) {
      endExpiredSession({ pathname, search: '', hash: '', replace });
    }
    expect(replace).not.toHaveBeenCalled();
  });
});

// The API client's own handling of an answer, through its interceptors: until this test only
// the functions they call ran in a test, not the wiring.
describe('a request through the API client', () => {
  const answers = vi.fn<(config: InternalAxiosRequestConfig) => Promise<never>>();
  let adapter: typeof api.defaults.adapter;

  beforeEach(() => {
    adapter = api.defaults.adapter;
    api.defaults.adapter = answers;
    // A sign-in page: the expired session is ended without navigating away, which jsdom cannot.
    window.history.replaceState(null, '', '/login');
    persistSession('session-token', 'analyst', 'viewer');
  });

  afterEach(() => {
    api.defaults.adapter = adapter;
    answers.mockReset();
    window.history.replaceState(null, '', '/');
  });

  const refusedWith = (status: number) =>
    answers.mockImplementation(async (config) => {
      throw Object.assign(new Error(`Request failed with status code ${status}`), { config, response: { status } });
    });

  it("carries the session's token, and ends the session only when the API refuses it (401)", async () => {
    refusedWith(500);
    await expect(api.get('/families')).rejects.toMatchObject({ response: { status: 500 } });
    expect(answers.mock.calls[0][0].headers.Authorization).toBe('Bearer session-token');
    expect(getAuthToken()).toBe('session-token');

    refusedWith(401);
    await expect(api.get('/families')).rejects.toMatchObject({ response: { status: 401 } });
    expect(getAuthToken()).toBeNull();
  });

  it('says the API cannot be reached when a request gets no answer', async () => {
    answers.mockImplementation(async (config) => {
      throw Object.assign(new Error('Network Error'), { config, request: {} });
    });

    await expect(api.get('/families')).rejects.toThrow(/^Unable to reach the API at /);
    // Not an answer from the API: the session stays.
    expect(getAuthToken()).toBe('session-token');
  });
});
