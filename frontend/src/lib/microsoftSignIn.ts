// Microsoft sign-in (SEC-4): the browser half of signing in with the hospital's or university's
// Microsoft account (Microsoft Entra ID, formerly Azure AD).
//
// The backend says whether CoGA uses it (GET /auth/config: the application ID, the directory's
// authority and CoGA's API scope; none of it secret). MSAL (@azure/msal-browser, loaded only
// when it is used) signs the user in with the authorization code flow with PKCE, by a full-page
// redirect to Microsoft and back to /login, and asks for an access token for CoGA's API scope.
// That token is the bearer of every API request; the API client asks MSAL for a fresh one
// before each request, so MSAL renews it (silently, with its refresh token) before it expires.
// MSAL keeps its tokens in this tab's session storage: closing the tab forgets them.
//
// The page to open after the sign-in never goes to Microsoft: the request carries only a random
// key, under which the path waits in this tab's session storage.
import { queryOptions } from '@tanstack/react-query';
import type { PublicClientApplication } from '@azure/msal-browser';
import api from './api';
import type { MicrosoftSignInOut, SignInConfigOut } from './apiSchema.generated';

export type MicrosoftSignIn = MicrosoftSignInOut;

const NEXT_PATH_PREFIX = 'coga.sign-in.next.';
const NEXT_PATH_KEY = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

let configRequest: Promise<SignInConfigOut> | null = null;
let clientRequest: Promise<PublicClientApplication> | null = null;
let redirectRequest: Promise<CompletedMicrosoftSignIn | null> | null = null;
let tokenRequest: Promise<string | null> | null = null;

/** How users sign in, as the backend says: with Microsoft, or (null) with a password. */
export const fetchSignInConfig = async (): Promise<SignInConfigOut> => {
  const response = await api.get('/auth/config');
  return { microsoft: (response?.data as SignInConfigOut | undefined)?.microsoft ?? null };
};

export const signInConfigQuery = queryOptions({
  queryKey: ['auth', 'sign-in-config'],
  queryFn: fetchSignInConfig,
  staleTime: Infinity,
  retry: false,
});

// The same, asked once per page load for the API client and the sign-out, outside React.
const sessionSignInConfig = (): Promise<SignInConfigOut> => {
  configRequest ??= fetchSignInConfig().catch((error: unknown) => {
    configRequest = null;
    throw error;
  });
  return configRequest;
};

const appUrl = (path: string): string => `${window.location.origin}${path}`;

const microsoftClient = (config: MicrosoftSignIn): Promise<PublicClientApplication> => {
  clientRequest ??= (async () => {
    const { PublicClientApplication } = await import('@azure/msal-browser');
    const client = new PublicClientApplication({
      auth: {
        clientId: config.client_id,
        authority: config.authority,
        // Registered in the app registration (docs/deployment-gcp.md): Microsoft sends the
        // user back to the sign-in page, and from there to the page they asked for.
        redirectUri: appUrl('/login'),
        postLogoutRedirectUri: appUrl('/login'),
      },
      cache: { cacheLocation: 'sessionStorage' },
    });
    await client.initialize();
    return client;
  })().catch((error: unknown) => {
    clientRequest = null;
    throw error;
  });
  return clientRequest;
};

const rememberNextPath = (nextPath: string): string => {
  const key = crypto.randomUUID();
  try {
    sessionStorage.setItem(`${NEXT_PATH_PREFIX}${key}`, nextPath);
  } catch {
    // Without session storage the sign-in opens the dashboard.
  }
  return key;
};

const recallNextPath = (key: string | undefined): string | null => {
  if (!key || !NEXT_PATH_KEY.test(key)) return null;
  try {
    const nextPath = sessionStorage.getItem(`${NEXT_PATH_PREFIX}${key}`);
    sessionStorage.removeItem(`${NEXT_PATH_PREFIX}${key}`);
    return nextPath;
  } catch {
    return null;
  }
};

/** Go to Microsoft to sign in; the browser comes back to /login, which completes it. */
export const startMicrosoftSignIn = async (config: MicrosoftSignIn, nextPath: string): Promise<void> => {
  const client = await microsoftClient(config);
  // MSAL starts no sign-in while it is still reading the page for one that came back.
  await redirectRequest?.catch(() => null);
  await client.loginRedirect({
    scopes: [config.api_scope],
    state: rememberNextPath(nextPath),
    // Always show which account continues, so a shared workstation never signs the next
    // person in as the previous one without asking.
    prompt: 'select_account',
  });
};

export interface CompletedMicrosoftSignIn {
  accessToken: string;
  // The page asked for before the sign-in, as remembered in this tab (not yet checked).
  nextPath: string | null;
}

/**
 * The sign-in Microsoft just sent the browser back with, or null when it brought none. Asked
 * twice (React runs an effect twice in development), it is completed once.
 */
export const completeMicrosoftSignIn = (config: MicrosoftSignIn): Promise<CompletedMicrosoftSignIn | null> => {
  redirectRequest ??= microsoftClient(config).then(async (client) => {
    const result = await client.handleRedirectPromise({ navigateToLoginRequestUrl: false });
    if (!result) return null;
    if (result.account) client.setActiveAccount(result.account);
    return { accessToken: result.accessToken, nextPath: recallNextPath(result.state) };
  });
  return redirectRequest;
};

/**
 * A valid access token for the signed-in Microsoft account, renewed by MSAL when the one it
 * holds is about to expire; null when there is none to be had without the user (then the API
 * answers 401 and the user signs in again). Requests asking at once share one renewal.
 */
export const freshMicrosoftAccessToken = (): Promise<string | null> => {
  tokenRequest ??= (async () => {
    try {
      const { microsoft } = await sessionSignInConfig();
      if (!microsoft) return null;
      const client = await microsoftClient(microsoft);
      const account = client.getActiveAccount() ?? client.getAllAccounts()[0];
      if (!account) return null;
      const result = await client.acquireTokenSilent({ scopes: [microsoft.api_scope], account });
      return result.accessToken || null;
    } catch {
      return null;
    } finally {
      tokenRequest = null;
    }
  })();
  return tokenRequest;
};

/** Forget the Microsoft account and its tokens in this tab (CoGA's sign-out). Microsoft's own
 * sign-in in this browser stays: signOutOfMicrosoft ends that too. */
export const forgetMicrosoftAccount = async (): Promise<void> => {
  try {
    const { microsoft } = await sessionSignInConfig();
    if (!microsoft) return;
    const client = await microsoftClient(microsoft);
    await client.clearCache();
  } catch {
    // Nothing to forget, or nothing that can be: the CoGA session is gone either way.
  }
};

/** Sign out of the Microsoft account in this browser too; Microsoft returns to /login. */
export const signOutOfMicrosoft = async (config: MicrosoftSignIn): Promise<void> => {
  const client = await microsoftClient(config);
  await client.logoutRedirect({ postLogoutRedirectUri: appUrl('/login') });
};

/** Tests only: forget the loaded configuration, MSAL client and pending sign-in. */
export const resetMicrosoftSignInForTests = (): void => {
  configRequest = null;
  clientRequest = null;
  redirectRequest = null;
  tokenRequest = null;
};
