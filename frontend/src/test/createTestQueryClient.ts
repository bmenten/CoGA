import { QueryClient, type QueryClientConfig } from '@tanstack/react-query';

export const createTestQueryClient = (config: QueryClientConfig = {}) => {
  const defaultOptions = config.defaultOptions ?? {};

  return new QueryClient({
    ...config,
    defaultOptions: {
      ...defaultOptions,
      queries: {
        retry: false,
        gcTime: Infinity,
        ...defaultOptions.queries,
      },
      mutations: {
        retry: false,
        gcTime: Infinity,
        ...defaultOptions.mutations,
      },
    },
  });
};
