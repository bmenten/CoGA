import { storage } from './storage';

export const AUTH_STORAGE_KEYS = {
  token: 'token',
  username: 'username',
  role: 'role',
} as const;

export type UserRole = 'admin' | 'viewer' | null;

export function getAuthToken(): string | null {
  return storage.getItem(AUTH_STORAGE_KEYS.token);
}

export function getStoredUsername(): string | null {
  return storage.getItem(AUTH_STORAGE_KEYS.username);
}

export function getStoredRole(): UserRole {
  const role = storage.getItem(AUTH_STORAGE_KEYS.role);
  return role === 'admin' || role === 'viewer' ? role : null;
}

export function isAuthenticated(): boolean {
  return Boolean(getAuthToken());
}

export function isAdmin(): boolean {
  return getStoredRole() === 'admin';
}

export function persistSession(token: string, username: string, role: string): void {
  storage.setItem(AUTH_STORAGE_KEYS.token, token);
  storage.setItem(AUTH_STORAGE_KEYS.username, username);
  storage.setItem(AUTH_STORAGE_KEYS.role, role);
}

export function clearSession(): void {
  storage.removeItem(AUTH_STORAGE_KEYS.token);
  storage.removeItem(AUTH_STORAGE_KEYS.username);
  storage.removeItem(AUTH_STORAGE_KEYS.role);
}
