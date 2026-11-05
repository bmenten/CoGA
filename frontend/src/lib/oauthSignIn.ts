// Sign-in through the organisation's OAuth 2.0 server (SEC-4): the authorization code flow
// with PKCE (RFC 6749 §4.1, RFC 7636).
//
// The backend says whether CoGA uses it (GET /auth/config: the server's authorization page for
// CoGA's client and, when it has one, its sign-out page; none of it secret). Before the browser
// goes there, this tab makes a one-time `state` and a code verifier and keeps them, with the page
// to open afterwards, in its session storage; the authorization page gets the state and the
// verifier's S256 challenge. The server sends the browser back to /login with a one-time code
// and the same state. The page takes the code only when the state is the one this tab kept
// (so nobody can sign this tab in with their own code), and hands it with the verifier to the
// API (POST /auth/oauth/session), which redeems it with the client secret and starts the
// session with a token of its own. The kept values are used once and then forgotten.
import api from './api';
import type { OAuthSignInOut } from './apiSchema.generated';

export type OAuthSignIn = OAuthSignInOut;

const PENDING_KEY = 'coga.oauth-sign-in';
// What the OAuth server writes back: a code of URL-safe characters, an error code (RFC 6749
// §4.1.2.1), and the state this tab made (base64url).
const AUTHORIZATION_CODE = /^[A-Za-z0-9._~+/=-]{1,2048}$/;
const ERROR_CODE = /^[a-z_]{1,40}$/;
const STATE = /^[A-Za-z0-9_-]{22,128}$/;

interface PendingSignIn {
  state: string;
  verifier: string;
  next: string;
}

/** Where the OAuth server sent the browser back: a code with its state, or a refusal. */
export type OAuthReturn = { code: string; state: string } | { error: string };

const base64Url = (bytes: Uint8Array): string => {
  let binary = '';
  bytes.forEach((byte) => {
    binary += String.fromCharCode(byte);
  });
  return btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
};

const randomValue = (byteCount: number): string => base64Url(crypto.getRandomValues(new Uint8Array(byteCount)));

const challengeFor = async (verifier: string): Promise<string> => {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier));
  return base64Url(new Uint8Array(digest));
};

/** What the query string of /login brings back from the OAuth server, if anything. */
export const oauthReturnFrom = (search: string): OAuthReturn | null => {
  const params = new URLSearchParams(search);
  const code = params.get('code');
  const state = params.get('state');
  if (code !== null && state !== null && AUTHORIZATION_CODE.test(code) && STATE.test(state)) {
    return { code, state };
  }
  const error = params.get('error');
  if (error !== null) {
    return { error: ERROR_CODE.test(error) ? error : 'unknown_error' };
  }
  return null;
};

/** Go to the OAuth server's authorization page; the sign-in comes back to /login. */
export const startOAuthSignIn = async (config: OAuthSignIn, nextPath: string): Promise<void> => {
  // 32 random bytes: a 43-character verifier, the shortest RFC 7636 allows, with 256 bits.
  const pending: PendingSignIn = { state: randomValue(32), verifier: randomValue(32), next: nextPath };
  const url = new URL(config.authorize_url);
  url.searchParams.set('state', pending.state);
  url.searchParams.set('code_challenge', await challengeFor(pending.verifier));
  url.searchParams.set('code_challenge_method', 'S256');
  try {
    window.sessionStorage.setItem(PENDING_KEY, JSON.stringify(pending));
  } catch {
    // Without the state and verifier kept in this tab, the code that comes back cannot be used.
    throw new Error('This browser does not let the page keep the sign-in: allow site data for CoGA and try again.');
  }
  window.location.assign(url.toString());
};

const takePending = (): PendingSignIn | null => {
  try {
    const raw = window.sessionStorage.getItem(PENDING_KEY);
    window.sessionStorage.removeItem(PENDING_KEY);
    const pending = raw ? (JSON.parse(raw) as Partial<PendingSignIn>) : null;
    if (
      pending &&
      typeof pending.state === 'string' &&
      typeof pending.verifier === 'string' &&
      typeof pending.next === 'string'
    ) {
      return { state: pending.state, verifier: pending.verifier, next: pending.next };
    }
  } catch {
    // Nothing usable was kept.
  }
  return null;
};

/** Forget a sign-in this tab started, as when the server refused it. */
export const forgetOAuthSignIn = (): void => {
  takePending();
};

export interface CompletedOAuthSignIn {
  accessToken: string;
  nextPath: string | null;
}

/** Have the API redeem the code and start the session; the page asked for before it. */
export const completeOAuthSignIn = async (code: string, state: string): Promise<CompletedOAuthSignIn> => {
  const pending = takePending();
  if (!pending || pending.state !== state) {
    throw new Error('This sign-in was not started in this tab. Sign in again.');
  }
  const response = await api.post('/auth/oauth/session', { authorization_code: code, code_verifier: pending.verifier });
  return { accessToken: String(response.data.access_token), nextPath: pending.next };
};
