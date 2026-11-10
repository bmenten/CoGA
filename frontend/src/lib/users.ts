import { queryOptions } from '@tanstack/react-query';
import api from './api';
import type { UserRead } from './apiSchema.generated';
import type { ApiUserRef } from './apiTypes';

/**
 * Human-friendly label for a user reference: "First Last" when a name is set,
 * otherwise the username, otherwise the email, otherwise an em dash.
 */
export const formatUserRef = (user?: ApiUserRef | null): string => {
  if (!user) return '—';
  const name = `${user.first_name ?? ''} ${user.last_name ?? ''}`.trim();
  return name || user.username || user.email || '—';
};

/**
 * The user accounts as the API serves them (`GET /auth/users`, administrators only). The Users,
 * Projects and Audit logs pages share this cache entry, so each reads it through this one
 * definition: a page that cached only part of an account (id and e-mail) under it would leave the
 * Users page opened next reading those as whole accounts, without their project list.
 */
export const userAccountsQuery = queryOptions({
  queryKey: ['admin', 'users'],
  queryFn: async (): Promise<UserRead[]> => {
    const response = await api.get('/auth/users');
    return response.data as UserRead[];
  },
});
