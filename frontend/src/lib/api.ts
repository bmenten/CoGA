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

api.interceptors.request.use(async (config) => {
  let token = getAuthToken();
  if (!token || !shouldAttachStoredToken(config.url, config.headers)) {
    return config;
  }
  if (getSessionSignIn() === 'microsoft') {
    // Microsoft's access token lives about an hour: MSAL hands back the one it holds, or a
    // renewed one when that is about to expire (SEC-4).
    const { freshMicrosoftAccessToken } = await import('./microsoftSignIn');
    const fresh = await freshMicrosoftAccessToken();
    if (fresh && fresh !== token) {
      updateSessionToken(fresh);
      token = fresh;
    }
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
