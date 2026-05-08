import { QueryClient } from '@tanstack/react-query';

export const APP_QUERY_STALE_TIME_MS = 30_000;
export const APP_QUERY_GC_TIME_MS = 10 * 60 * 1000;

export const createAppQueryClient = () =>
  new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: APP_QUERY_STALE_TIME_MS,
        gcTime: APP_QUERY_GC_TIME_MS,
        refetchOnWindowFocus: false,
      },
      mutations: {
        gcTime: APP_QUERY_GC_TIME_MS,
      },
    },
  });
