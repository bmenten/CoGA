import axios from 'axios';
import { clearSession, getAuthToken, getSessionSignIn, signInPath, updateSessionToken } from './auth';
import { buildApiUnavailableMessage, isNetworkTransportError } from './errorMessage';

const DEFAULT_API_BASE_URL = '/api';
export const apiBaseUrl = import.meta.env.VITE_API_BASE_URL?.trim() || DEFAULT_API_BASE_URL;
// Asked without a session's token: the sign-in, the sign-up and how users sign in.
const AUTH_EXCLUDED_PATHS = new Set(['/auth/login', '/auth/signup', '/auth/config']);

export const isAbsoluteUrl = (value: string): boolean => /^https?:\/\//i.test(value);

export const hasAuthorizationHeader = (headers: unknown): boolean => {
  if (!headers || typeof headers !== 'object') {
    return false;
  }

  return Object.keys(headers as Record<string, unknown>).some(
    (key) => key.toLowerCase() === 'authorization'
  );
};

export const shouldAttachStoredToken = (url?: string, headers?: unknown): boolean => {
  if (hasAuthorizationHeader(headers)) {
    return false;
  }

  if (!url) {
    return true;
  }

  const normalizedUrl = url.startsWith('http') ? new URL(url).pathname : url;
  return !AUTH_EXCLUDED_PATHS.has(normalizedUrl);
};

export const normalizeApiTransportError = (error: unknown, baseUrl = apiBaseUrl): unknown => {
  if (isNetworkTransportError(error) && error instanceof Error) {
    error.message = buildApiUnavailableMessage(baseUrl);
  }
  return error;
};

const api = axios.create({
  baseURL: apiBaseUrl,
});

/**
 * Resolve a URL the API handed back for the browser to fetch directly.
 *
 * Some endpoints return a *location* instead of the bytes (alignments for IGV, the
 * pipeline QC report). In object-storage mode those are absolute presigned URLs and
 * must be used as-is; otherwise they are **backend-relative** and need the API base
 * prefixed. Handing such a path straight to `window.open` makes the browser resolve it
 * against the SPA origin, where the client router claims it and renders "page not
 * found" instead of the request ever reaching the backend.
 */
export const resolveApiUrl = (
  value: string,
  base: string = api.defaults.baseURL ?? apiBaseUrl,
): string => (isAbsoluteUrl(value) ? value : `${base}${value}`);

/**
 * The bearer token for a request of this session, or null without a session: the token the
 * session holds or, signed in with Microsoft, the one MSAL hands back, which the session then
 * keeps. Asked for at each request, the API client's and IGV's alike (SEC-32): a token read
 * once runs out while the page stays open.
 */
export const sessionAccessToken = async (): Promise<string | null> => {
  const token = getAuthToken();
  if (!token || getSessionSignIn() !== 'microsoft') {
    return token;
  }
  // Microsoft's access token lives about an hour: MSAL hands back the one it holds, or a
  // renewed one when that is about to expire (SEC-4).
  const { freshMicrosoftAccessToken } = await import('./microsoftSignIn');
  const fresh = await freshMicrosoftAccessToken();
  if (fresh && fresh !== token) {
    updateSessionToken(fresh);
    return fresh;
  }
  return token;
};

api.interceptors.request.use(async (config) => {
  if (!getAuthToken() || !shouldAttachStoredToken(config.url, config.headers)) {
    return config;
  }
  const token = await sessionAccessToken();
  if (!token) {
    return config;
  }
  config.headers = config.headers ?? {};
  config.headers.Authorization = `Bearer ${token}`;
  return config;
});

type PageLocation = Pick<Location, 'pathname' | 'search' | 'hash' | 'replace'>;

/**
 * A session the API refused (401): cleared, and the user sent to sign in, which returns to the
 * page the session expired on, as a sign-in RequireAuth sends does (UI-3: it opened the
 * dashboard). Not from a sign-in page (/login, /login/emergency): it shows the refusal itself.
 */
export const endExpiredSession = (
  location: PageLocation | undefined = typeof window !== 'undefined' ? window.location : undefined,
): void => {
  clearSession();
  if (location && !/^\/login(\/|$)/.test(location.pathname)) {
    location.replace(signInPath(`${location.pathname}${location.search}${location.hash}`));
  }
};

// How long a logout waits for the API to answer the UI events it flushed before it ends the
// session on the server, which refuses any of them that come after.
export const LOGOUT_WAITS_FOR_EVENTS_MS = 2000;

/**
 * End the session on the server too (`POST /auth/logout`): every token issued to the account
 * before then is refused, in every browser and on every device, a copy of this one included, so
 * a token copied from the browser stops working at the logout instead of at its expiry. `token`
 * is the session's, taken before the logout forgot it; `after` is the logout's flush of the UI
 * events. The request goes once that flush is answered, or after `LOGOUT_WAITS_FOR_EVENTS_MS`,
 * whichever comes first, and at once when the page is closed meanwhile; as a keepalive, it
 * survives the page. Best-effort: a request that fails (offline, or the token had expired) ends
 * nothing on the server, and the session's tokens stay valid until they expire, as before.
 * Settles once it has been answered or failed.
 */
export const endServerSession = (
  token: string | null,
  after: Promise<unknown> = Promise.resolve(),
): Promise<void> => {
  if (!token) return Promise.resolve();
  let answered: () => void = () => undefined;
  const done = new Promise<void>((resolve) => {
    answered = resolve;
  });
  let sent = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const send = (): void => {
    if (sent) return;
    sent = true;
    clearTimeout(timer);
    window.removeEventListener('pagehide', send);
    try {
      void fetch(`${apiBaseUrl}/auth/logout`, {
        method: 'POST',
        headers: { Authorization: `Bearer ${token}` },
        keepalive: true,
      }).then(
        () => answered(),
        () => answered(),
      );
    } catch {
      answered();
    }
  };
  window.addEventListener('pagehide', send);
  const waited = new Promise<void>((resolve) => {
    timer = setTimeout(resolve, LOGOUT_WAITS_FOR_EVENTS_MS);
  });
  void Promise.race([Promise.resolve(after).catch(() => undefined), waited]).then(send);
  return done;
};

api.interceptors.response.use(
  (response) => response,
  (error) => {
    normalizeApiTransportError(error, api.defaults.baseURL);
    if (error?.response?.status === 401) {
      endExpiredSession();
    }
    return Promise.reject(error);
  }
);

export default api;
