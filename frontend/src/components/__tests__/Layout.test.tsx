// Logout — #521: it cleared the session but left the previous user's query cache.

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import Layout from '../Layout';
import { createTestQueryClient } from '../../test/createTestQueryClient';

const telemetry = vi.hoisted(() => ({ flushUiEventsNow: vi.fn() }));
vi.mock('../../lib/telemetry', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/telemetry')>()),
  flushUiEventsNow: telemetry.flushUiEventsNow,
}));

describe('Layout logout', () => {
  it('flushes telemetry, then clears the cached data and the session', () => {
    localStorage.setItem('token', 'token-1');
    localStorage.setItem('username', 'alice@example.org');
    const queryClient = createTestQueryClient();
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1' });

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/dashboard']}>
          <Routes>
            <Route element={<Layout />}>
              <Route path="/dashboard" element={<p>Dashboard</p>} />
            </Route>
            <Route path="/login" element={<p>Login page</p>} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    fireEvent.click(screen.getByRole('button', { name: /logout/i }));

    expect(telemetry.flushUiEventsNow).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
    expect(localStorage.getItem('token')).toBeNull();
    expect(screen.getByText('Login page')).toBeInTheDocument();
  });
});
