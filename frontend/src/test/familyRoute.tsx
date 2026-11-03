import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';

import { createTestQueryClient } from './createTestQueryClient';

/**
 * Renders `page` at `url` under its route `path` (`/families/:familyId/…`), as the app mounts a
 * family page: inside a router and a query client of its own, unless the test passes the one it
 * reads the cache of.
 */
export const renderFamilyRoute = (
  path: string,
  url: string,
  page: ReactElement,
  queryClient: QueryClient = createTestQueryClient(),
) =>
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          <Route path={path} element={page} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
