import { render as rtlRender, screen, fireEvent, waitFor } from '@testing-library/react';
import { QueryClientProvider, type QueryClient } from '@tanstack/react-query';
import type { ReactElement } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, it, vi, type Mock } from 'vitest';
import LoginPage from '../LoginPage';
import api from '../../../lib/api';
import { clearSession } from '../../../lib/auth';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

// LoginPage clears the query cache when a session starts (#521), so it needs a client.
let queryClient: QueryClient;
const render = (ui: ReactElement) =>
  rtlRender(<QueryClientProvider client={queryClient}>{ui}</QueryClientProvider>);

vi.mock('../../../lib/api');

describe('LoginPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    clearSession();
    localStorage.clear();
    queryClient = createTestQueryClient();
  });

  it('renders login form inputs and button', () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    expect(screen.getByPlaceholderText(/email/i)).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/password/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /login/i })).toBeInTheDocument();
  });

  it('redirects to the dashboard when a session is already stored', () => {
    localStorage.setItem('token', 'token-123');

    render(
      <MemoryRouter initialEntries={['/login']}>
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route path="/dashboard" element={<div>Dashboard landing</div>} />
        </Routes>
      </MemoryRouter>
    );

    expect(screen.getByText('Dashboard landing')).toBeInTheDocument();
  });

  it('shows error message on failed login', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      response: { data: { detail: 'Incorrect email or password' } },
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'wrong' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));
    expect(
      await screen.findByText(/incorrect email or password/i)
    ).toBeInTheDocument();
  });

  it('shows formatted validation errors from the API', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      response: {
        data: {
          detail: [
            {
              type: 'string_too_short',
              loc: ['body', 'password'],
              msg: 'String should have at least 8 characters',
              input: 'short',
              ctx: { min_length: 8 },
            },
          ],
        },
      },
    });
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'short' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));
    expect(
      await screen.findByText(/password: string should have at least 8 characters/i)
    ).toBeInTheDocument();
  });

  it('shows an explicit API unavailable message on transport failure', async () => {
    (api.post as unknown as Mock).mockRejectedValue({
      request: {},
      message: 'Network Error',
    });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'admin' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    expect(
      await screen.findByText(
        /unable to reach the api at \/api\. check that the backend, postgres, and clickhouse services are running\./i
      )
    ).toBeInTheDocument();
  });

  it('uses the fresh access token when loading the current user after login', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        access_token: 'token-123',
        role: 'admin',
      },
    });
    (api.get as unknown as Mock).mockResolvedValue({
      data: {
        email: 'admin@example.com',
        role: 'admin',
      },
    });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'admin@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'admin' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() =>
      expect(api.get).toHaveBeenCalledWith('/auth/me', {
        headers: {
          Authorization: 'Bearer token-123',
        },
      })
    );
    expect(localStorage.getItem('token')).toBe('token-123');
    expect(localStorage.getItem('role')).toBe('admin');
  });

  it('starts the new session with an empty query cache (#521)', async () => {
    // Data an earlier session loaded in this tab must not be shown to the next user.
    queryClient.setQueryData(['family', 'F1'], { family_id: 'F1', members: ['someone else'] });
    (api.post as unknown as Mock).mockResolvedValue({ data: { access_token: 'token-456' } });
    (api.get as unknown as Mock).mockResolvedValue({ data: { email: 'next@example.com', role: 'viewer' } });

    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>
    );
    fireEvent.change(screen.getByPlaceholderText(/email/i), { target: { value: 'next@example.com' } });
    fireEvent.change(screen.getByPlaceholderText(/password/i), { target: { value: 'pw' } });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    await waitFor(() => expect(localStorage.getItem('token')).toBe('token-456'));
    expect(queryClient.getQueryData(['family', 'F1'])).toBeUndefined();
  });

  it('continues to the requested protected page after login', async () => {
    (api.post as unknown as Mock).mockResolvedValue({
      data: {
        access_token: 'token-123',
        role: 'viewer',
      },
    });
    (api.get as unknown as Mock).mockResolvedValue({
      data: {
        email: 'viewer@example.com',
        role: 'viewer',
      },
    });

    render(
      <MemoryRouter
        initialEntries={[
          '/login?next=/families/demo_family/small-variants%3Fpage%3D2',
        ]}
      >
        <Routes>
          <Route path="/login" element={<LoginPage />} />
          <Route
            path="/families/:familyId/small-variants"
            element={<div>Small variant workspace</div>}
          />
        </Routes>
      </MemoryRouter>
    );

    expect(
      screen.getByText(/sign in to continue to your requested page/i)
    ).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText(/email/i), {
      target: { value: 'viewer@example.com' },
    });
    fireEvent.change(screen.getByPlaceholderText(/password/i), {
      target: { value: 'viewer-password' },
    });
    fireEvent.click(screen.getByRole('button', { name: /login/i }));

    expect(await screen.findByText('Small variant workspace')).toBeInTheDocument();
  });
});
