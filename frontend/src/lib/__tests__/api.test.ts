import type { InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api, {
  endExpiredSession,
  endServerSession,
  hasAuthorizationHeader,
  LOGOUT_WAITS_FOR_EVENTS_MS,
  normalizeApiTransportError,
  shouldAttachStoredToken,
} from '../api';
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
  it('signs in again to the page the session expired on', () => {
    persistSession('session-token', 'analyst', 'viewer');
    const replace = vi.fn();
    endExpiredSession({ pathname: '/families/F1/small-variants', search: '?project_id=P1', hash: '#v2', replace });

    expect(getAuthToken()).toBeNull();
    // As RequireAuth writes it: the page's path, query string and hash, percent-encoded, never the
    // dashboard.
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

// The API client's own handling of an answer, through its interceptors: the wiring, not only
// the functions they call.
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

// The logout ends the session on the server too (POST /auth/logout): every token of the account
// is refused from then on, on every device. It goes after the logout's last UI events are
// answered, which a token it ended would see refused.
describe('the logout on the server', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  const deferred = () => {
    let resolve: () => void = () => undefined;
    const promise = new Promise<void>((done) => {
      resolve = done;
    });
    return { promise, resolve };
  };

  it("goes once the flushed events are answered, with the session's token, surviving the page", async () => {
    const flushed = deferred();
    const ended = endServerSession('session-token', flushed.promise);
    await Promise.resolve();
    expect(fetchMock).not.toHaveBeenCalled();

    flushed.resolve();
    await ended;

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/auth/logout');
    expect(init.method).toBe('POST');
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer session-token');
  });

  it('goes after a short wait when the events are not answered, and once only', async () => {
    vi.useFakeTimers();
    const ended = endServerSession('session-token', new Promise<void>(() => undefined));

    await vi.advanceTimersByTimeAsync(LOGOUT_WAITS_FOR_EVENTS_MS - 1);
    expect(fetchMock).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1);
    await ended;
    window.dispatchEvent(new Event('pagehide'));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('goes at once when the page is closed while it waits', async () => {
    vi.useFakeTimers();
    void endServerSession('session-token', new Promise<void>(() => undefined));

    window.dispatchEvent(new Event('pagehide'));
    expect(fetchMock).toHaveBeenCalledTimes(1);

    await vi.advanceTimersByTimeAsync(LOGOUT_WAITS_FOR_EVENTS_MS);
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('goes also when the flush failed, and ends quietly when the request does', async () => {
    fetchMock.mockRejectedValueOnce(new TypeError('Failed to fetch'));

    await expect(endServerSession('session-token', Promise.reject(new Error('flush failed')))).resolves.toBeUndefined();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('sends nothing without a token', async () => {
    await endServerSession(null);

    expect(fetchMock).not.toHaveBeenCalled();
  });
});
