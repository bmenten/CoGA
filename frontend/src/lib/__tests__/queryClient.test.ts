import { describe, expect, it } from 'vitest';

import {
  APP_QUERY_GC_TIME_MS,
  APP_QUERY_STALE_TIME_MS,
  createAppQueryClient,
} from '../queryClient';

describe('createAppQueryClient', () => {
  it('installs cache and refetch defaults tuned for interactive navigation', () => {
    const client = createAppQueryClient();
    const defaults = client.getDefaultOptions();

    expect(defaults.queries?.staleTime).toBe(APP_QUERY_STALE_TIME_MS);
    expect(defaults.queries?.gcTime).toBe(APP_QUERY_GC_TIME_MS);
    expect(defaults.queries?.refetchOnWindowFocus).toBe(false);
    expect(defaults.mutations?.gcTime).toBe(APP_QUERY_GC_TIME_MS);
  });
});
