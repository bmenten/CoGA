import { storage } from './storage';

export const AUTH_STORAGE_KEYS = {
  token: 'token',
  username: 'username',
  role: 'role',
  // How this session signed in: with Microsoft, whose access token the API client renews
  // before it expires, through the organisation's CAS server, or with a password. The
  // last two hold a token this server issued.
  signIn: 'sign_in',
} as const;

/**
 * The global roles, lowest first; each has every right of the roles before it. A viewer reads
 * the records of their projects; an analyst also analyses them; a supervisor also signs an
 * analysis out; a superuser also has every project and the configuration the analyses read
 * (gene panels, variant tags, filter presets, sequencing QC thresholds, package import); an
 * admin, the whole administration. The server decides; the screens only follow it.
 */
export const USER_ROLES = ['viewer', 'analyst', 'supervisor', 'superuser', 'admin'] as const;

export type KnownUserRole = (typeof USER_ROLES)[number];

export type UserRole = KnownUserRole | null;

function isKnownRole(role: string | null): role is KnownUserRole {
  return (USER_ROLES as readonly string[]).includes(role ?? '');
}

export type SignInMethod = 'microsoft' | 'cas' | 'password';

export function getAuthToken(): string | null {
  return storage.getItem(AUTH_STORAGE_KEYS.token);
}

export function getStoredUsername(): string | null {
  return storage.getItem(AUTH_STORAGE_KEYS.username);
}

export function getStoredRole(): UserRole {
  const role = storage.getItem(AUTH_STORAGE_KEYS.role);
  return isKnownRole(role) ? role : null;
}

/** Whether the signed-in user's role is `role` or a higher one. */
export function hasRole(role: KnownUserRole): boolean {
  const stored = getStoredRole();
  return stored !== null && USER_ROLES.indexOf(stored) >= USER_ROLES.indexOf(role);
}

export function getSessionSignIn(): SignInMethod {
  const signIn = storage.getItem(AUTH_STORAGE_KEYS.signIn);
  return signIn === 'microsoft' || signIn === 'cas' ? signIn : 'password';
}

export function isAuthenticated(): boolean {
  return Boolean(getAuthToken());
}

/** Whether the signed-in user has the whole administration: an admin. */
export function isAdmin(): boolean {
  return getStoredRole() === 'admin';
}

/**
 * Whether the signed-in user has the configuration the analyses read and package import, and
 * so the admin dashboard with those workspaces: a superuser or an admin.
 */
export function hasSuperuserRights(): boolean {
  return hasRole('superuser');
}

export function persistSession(
  token: string,
  username: string,
  role: string,
  signIn: SignInMethod = 'password',
): void {
  storage.setItem(AUTH_STORAGE_KEYS.token, token);
  storage.setItem(AUTH_STORAGE_KEYS.username, username);
  storage.setItem(AUTH_STORAGE_KEYS.role, role);
  storage.setItem(AUTH_STORAGE_KEYS.signIn, signIn);
}

/** Keep a renewed access token for the session that holds one; no session, no token. */
export function updateSessionToken(token: string): void {
  if (getAuthToken()) {
    storage.setItem(AUTH_STORAGE_KEYS.token, token);
  }
}

export function clearSession(): void {
  storage.removeItem(AUTH_STORAGE_KEYS.token);
  storage.removeItem(AUTH_STORAGE_KEYS.username);
  storage.removeItem(AUTH_STORAGE_KEYS.role);
  storage.removeItem(AUTH_STORAGE_KEYS.signIn);
}

/**
 * Call `onEnded` when another tab of this browser ends the session the tabs share: its logout, or
 * the 401 that ended it there, removes the token (or the storage is cleared), and a sign-in there
 * of another account replaces the user name, the account's address. A renewal of the token writes
 * no user name, and a sign-in of the same account writes the one there is: the session goes on.
 * The browser tells a tab of the changes the other tabs make to its storage, each key with its old
 * and new value. Returns the function that stops it.
 */
export function onSessionEndedElsewhere(onEnded: () => void): () => void {
  const listener = (event: StorageEvent): void => {
    const ended = (event.key === AUTH_STORAGE_KEYS.token || event.key === null) && event.newValue === null;
    const otherAccount =
      event.key === AUTH_STORAGE_KEYS.username &&
      event.oldValue !== null &&
      event.newValue !== null &&
      event.newValue !== event.oldValue;
    if (ended || otherAccount) {
      onEnded();
    }
  };
  window.addEventListener('storage', listener);
  return () => window.removeEventListener('storage', listener);
}

/**
 * The sign-in page that returns to `page` (a path with its query string and hash, as the
 * browser holds it) once the user has signed in. The sign-in follows only a page of this site
 * (LoginPage, REQ-UI-006).
 */
export function signInPath(page: string): string {
  return `/login?next=${encodeURIComponent(page)}`;
}
