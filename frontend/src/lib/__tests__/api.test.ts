import { describe, expect, it, vi } from 'vitest';

import { endExpiredSession, hasAuthorizationHeader, normalizeApiTransportError, shouldAttachStoredToken } from '../api';
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
