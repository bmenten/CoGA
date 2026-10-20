// Microsoft sign-in in the browser (SEC-4), MSAL replaced by a stand-in.
//
// A session signed in with Microsoft gets its bearer from MSAL before every API request: the
// access token MSAL holds, or a renewed one, which the session then keeps; requests asking at
// once share one renewal. Without an account, or when MSAL cannot renew without the user, the
// request goes with the token the session holds (the API then answers 401 and the user signs in
// again). A password session never asks MSAL, and the requests that need no session's token
// (how users sign in, the sign-in and the sign-up) carry none. Signing out of CoGA forgets the
// Microsoft account in the tab. Values are made up; tokens are put together here.
import type { AxiosAdapter, InternalAxiosRequestConfig } from 'axios';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import api from '../api';
import { getAuthToken, persistSession } from '../auth';
import {
  forgetMicrosoftAccount,
  freshMicrosoftAccessToken,
  resetMicrosoftSignInForTests,
} from '../microsoftSignIn';

const msal = vi.hoisted(() => ({
  client: {
    initialize: vi.fn(),
    getActiveAccount: vi.fn(),
    getAllAccounts: vi.fn(),
    acquireTokenSilent: vi.fn(),
    clearCache: vi.fn(),
  },
  constructed: 0,
}));

vi.mock('@azure/msal-browser', () => ({
  PublicClientApplication: class {
    constructor() {
      msal.constructed += 1;
      return msal.client;
    }
  },
}));

const MICROSOFT = {
  client_id: 'coga-application',
  authority: 'https://login.microsoftonline.com/coga-directory',
  api_scope: 'api://coga-application/access_as_user',
};
const ACCOUNT = { homeAccountId: 'home', username: 'a.colleague@example.org' };
const held = ['held', 'access'].join('-');
const renewed = ['renewed', 'access'].join('-');

// What the API saw: each request's path and Authorization header.
let requests: Array<{ url: string | undefined; authorization: unknown }> = [];
let microsoft: typeof MICROSOFT | null = MICROSOFT;
const originalAdapter = api.defaults.adapter;

const adapter: AxiosAdapter = async (config: InternalAxiosRequestConfig) => {
  requests.push({ url: config.url, authorization: config.headers?.Authorization });
  const data = config.url === '/auth/config' ? { microsoft } : { ok: true };
  return { data, status: 200, statusText: 'OK', headers: {}, config };
};

beforeEach(() => {
  vi.resetAllMocks();
  resetMicrosoftSignInForTests();
  localStorage.clear();
  requests = [];
  microsoft = MICROSOFT;
  msal.constructed = 0;
  msal.client.initialize.mockResolvedValue(undefined);
  msal.client.getActiveAccount.mockReturnValue(ACCOUNT);
  msal.client.getAllAccounts.mockReturnValue([ACCOUNT]);
  msal.client.clearCache.mockResolvedValue(undefined);
  api.defaults.adapter = adapter;
});

afterEach(() => {
  api.defaults.adapter = originalAdapter;
  localStorage.clear();
});

describe('freshMicrosoftAccessToken', () => {
  it('asks MSAL silently for CoGA’s API scope, for the signed-in account', async () => {
    msal.client.acquireTokenSilent.mockResolvedValue({ accessToken: renewed });

    expect(await freshMicrosoftAccessToken()).toBe(renewed);
    expect(msal.client.acquireTokenSilent).toHaveBeenCalledWith({
      scopes: ['api://coga-application/access_as_user'],
      account: ACCOUNT,
    });
  });

  it('takes the account in the tab when none is active', async () => {
    msal.client.getActiveAccount.mockReturnValue(null);
    msal.client.acquireTokenSilent.mockResolvedValue({ accessToken: renewed });

    expect(await freshMicrosoftAccessToken()).toBe(renewed);
    expect(msal.client.acquireTokenSilent.mock.calls[0][0].account).toBe(ACCOUNT);
  });

  it('shares one renewal between requests that ask at once', async () => {
    let finish: (value: unknown) => void = () => undefined;
    msal.client.acquireTokenSilent.mockReturnValue(new Promise((resolve) => (finish = resolve)));

    const asked = [freshMicrosoftAccessToken(), freshMicrosoftAccessToken(), freshMicrosoftAccessToken()];
    await vi.waitFor(() => expect(msal.client.acquireTokenSilent).toHaveBeenCalledTimes(1));
    finish({ accessToken: renewed });

    expect(await Promise.all(asked)).toEqual([renewed, renewed, renewed]);
    expect(msal.client.acquireTokenSilent).toHaveBeenCalledTimes(1);
  });

  it('gives nothing without an account, or when MSAL cannot renew without the user', async () => {
    msal.client.getActiveAccount.mockReturnValue(null);
    msal.client.getAllAccounts.mockReturnValue([]);
    expect(await freshMicrosoftAccessToken()).toBeNull();
    expect(msal.client.acquireTokenSilent).not.toHaveBeenCalled();

    msal.client.getActiveAccount.mockReturnValue(ACCOUNT);
    msal.client.acquireTokenSilent.mockRejectedValue(new Error('interaction_required'));
    expect(await freshMicrosoftAccessToken()).toBeNull();
  });

  it('loads no MSAL without Microsoft sign-in', async () => {
    microsoft = null;

    expect(await freshMicrosoftAccessToken()).toBeNull();
    expect(msal.constructed).toBe(0);
  });
});

describe('the API client', () => {
  it('sends a Microsoft session’s renewed token, and keeps it for the session', async () => {
    persistSession(held, 'a.colleague@example.org', 'viewer', 'microsoft');
    msal.client.acquireTokenSilent.mockResolvedValue({ accessToken: renewed });

    await api.get('/families/');

    expect(requests).toEqual([
      // How users sign in, asked once, with no session's token.
      { url: '/auth/config', authorization: undefined },
      { url: '/families/', authorization: `Bearer ${renewed}` },
    ]);
    expect(getAuthToken()).toBe(renewed);
  });

  it('sends the token the session holds when MSAL has none to give', async () => {
    persistSession(held, 'a.colleague@example.org', 'viewer', 'microsoft');
    msal.client.acquireTokenSilent.mockRejectedValue(new Error('interaction_required'));

    await api.get('/families/');

    expect(requests.at(-1)).toEqual({ url: '/families/', authorization: `Bearer ${held}` });
    expect(getAuthToken()).toBe(held);
  });

  it('never asks MSAL for a password session', async () => {
    persistSession(held, 'admin@example.org', 'admin');

    await api.get('/families/');

    expect(requests).toEqual([{ url: '/families/', authorization: `Bearer ${held}` }]);
    expect(msal.constructed).toBe(0);
  });

  it('sends no session token with the requests that need none', async () => {
    persistSession(held, 'a.colleague@example.org', 'viewer', 'microsoft');

    await api.get('/auth/config');

    expect(requests).toEqual([{ url: '/auth/config', authorization: undefined }]);
    expect(msal.client.acquireTokenSilent).not.toHaveBeenCalled();
  });
});

describe('forgetMicrosoftAccount', () => {
  it('clears MSAL’s accounts and tokens in the tab', async () => {
    await forgetMicrosoftAccount();
    expect(msal.client.clearCache).toHaveBeenCalledTimes(1);
  });

  it('does nothing without Microsoft sign-in, and never fails the sign-out', async () => {
    microsoft = null;
    await forgetMicrosoftAccount();
    expect(msal.constructed).toBe(0);

    resetMicrosoftSignInForTests();
    microsoft = MICROSOFT;
    msal.client.clearCache.mockRejectedValue(new Error('storage unavailable'));
    await expect(forgetMicrosoftAccount()).resolves.toBeUndefined();
  });
});
