// Pins self-registration: exactly what is POSTed to /auth/signup, the hand-off to /login, how a
// refusal is shown — and that the password stays out of the URL and out of the console.
import { render, screen, waitFor } from '@testing-library/react';
import userEvent, { type UserEvent } from '@testing-library/user-event';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import SignupPage from '../SignupPage';
import api from '../../../lib/api';

vi.mock('../../../lib/api', () => ({
  default: { post: vi.fn(), defaults: { baseURL: '/api' } },
}));

const mockedPost = vi.mocked(api.post);

// Obviously fake: a secret scanner runs over the history.
const PASSWORD = 'test-password-1';

const REGISTRATION: Record<string, string> = {
  'First Name': 'Ann',
  'Last Name': 'Lee',
  Email: 'ann.lee@example.com',
  Affiliation: 'CMGG',
  Password: PASSWORD,
};

const RATE_LIMITED_DETAIL = 'Too many signup attempts. Try again later.';
const RATE_LIMITED = { response: { status: 429, data: { detail: RATE_LIMITED_DETAIL } } };

const LoginLanding = () => {
  const location = useLocation();
  return <p>Login page at {`${location.pathname}${location.search}${location.hash}`}</p>;
};

const renderPage = () =>
  render(
    <MemoryRouter initialEntries={['/signup']}>
      <Routes>
        <Route path="/signup" element={<SignupPage />} />
        <Route path="/login" element={<LoginLanding />} />
      </Routes>
    </MemoryRouter>,
  );

const fillIn = async (user: UserEvent, values: Record<string, string> = REGISTRATION) => {
  for (const [label, value] of Object.entries(values)) {
    await user.type(screen.getByLabelText(label), value);
  }
};

const submit = (user: UserEvent) => user.click(screen.getByRole('button', { name: 'Sign Up' }));

const stringify = (value: unknown): string => {
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value) ?? String(value);
  } catch {
    return String(value);
  }
};

describe('SignupPage', () => {
  beforeEach(() => {
    mockedPost.mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  it('posts every field to /auth/signup, the password only in the request body', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: { detail: 'Registration received.' } });
    renderPage();

    await fillIn(user);
    await submit(user);

    await waitFor(() => expect(mockedPost).toHaveBeenCalledTimes(1));
    // No request config: nothing — the password included — rides along as query params.
    expect(mockedPost.mock.calls[0]).toEqual([
      '/auth/signup',
      {
        email: 'ann.lee@example.com',
        password: PASSWORD,
        first_name: 'Ann',
        last_name: 'Lee',
        affiliation: 'CMGG',
      },
    ]);
  });

  it('masks the password as it is typed', () => {
    renderPage();

    expect(screen.getByLabelText('Password')).toHaveAttribute('type', 'password');
  });

  it('hands a registered user to the login page with nothing carried in the URL', async () => {
    const user = userEvent.setup();
    mockedPost.mockResolvedValue({ data: { detail: 'Registration received.' } });
    renderPage();

    await fillIn(user);
    await submit(user);

    // Exact match: no query string or fragment, so the password cannot be in either.
    expect(await screen.findByText('Login page at /login')).toBeInTheDocument();
  });

  it('shows the API’s reason when a registration is refused, announced, and stays on the form', async () => {
    const user = userEvent.setup();
    mockedPost.mockRejectedValue(RATE_LIMITED);
    renderPage();

    await fillIn(user);
    await submit(user);

    const error = await screen.findByText(RATE_LIMITED_DETAIL);
    expect(error).toHaveAttribute('aria-live', 'polite');
    expect(screen.getByRole('heading', { name: 'Create account' })).toBeInTheDocument();
    expect(screen.queryByText(/^Login page at/)).not.toBeInTheDocument();
  });

  it('shows the API’s field validation error against the field it concerns', async () => {
    const user = userEvent.setup();
    mockedPost.mockRejectedValue({
      response: {
        status: 422,
        data: {
          detail: [
            {
              type: 'value_error',
              loc: ['body', 'email'],
              msg: 'value is not a valid email address: An email address must have an @-sign.',
              input: 'not-an-email',
            },
          ],
        },
      },
    });
    renderPage();

    await fillIn(user, { ...REGISTRATION, Email: 'not-an-email' });
    await submit(user);

    expect(
      await screen.findByText(
        'email: value is not a valid email address: An email address must have an @-sign.',
      ),
    ).toBeInTheDocument();
  });

  it('says the API is unreachable when the request got no answer', async () => {
    const user = userEvent.setup();
    mockedPost.mockRejectedValue({ request: {}, message: 'Network Error' });
    renderPage();

    await fillIn(user);
    await submit(user);

    expect(
      await screen.findByText(
        'Unable to reach the API at /api. Check that the backend, Postgres, and ClickHouse services are running.',
      ),
    ).toBeInTheDocument();
  });

  it('falls back to "Sign up failed" when the error says nothing', async () => {
    const user = userEvent.setup();
    mockedPost.mockRejectedValue({});
    renderPage();

    await fillIn(user);
    await submit(user);

    expect(await screen.findByText('Sign up failed')).toBeInTheDocument();
  });

  it('clears the previous error as soon as the form is sent again', async () => {
    const user = userEvent.setup();
    mockedPost.mockRejectedValueOnce(RATE_LIMITED);
    // The retry is still in flight when the assertion runs.
    mockedPost.mockReturnValueOnce(new Promise(() => undefined));
    renderPage();

    await fillIn(user);
    await submit(user);
    expect(await screen.findByText(RATE_LIMITED_DETAIL)).toBeInTheDocument();

    await submit(user);

    await waitFor(() => expect(screen.queryByText(RATE_LIMITED_DETAIL)).not.toBeInTheDocument());
    expect(mockedPost).toHaveBeenCalledTimes(2);
  });

  it('links an already registered user back to the login page', () => {
    renderPage();

    expect(screen.getByRole('link', { name: 'Return to login' })).toHaveAttribute('href', '/login');
  });

  it('writes nothing to the console in a production build, although the failed request carries the password', async () => {
    vi.stubEnv('DEV', false);
    vi.stubEnv('MODE', 'production');
    const consoleSpies = (['error', 'warn', 'log', 'info', 'debug'] as const).map((method) =>
      vi.spyOn(console, method).mockImplementation(() => undefined),
    );
    const user = userEvent.setup();
    // Shaped like an Axios error, which travels with the serialised request body.
    const failure = {
      ...RATE_LIMITED,
      message: 'Request failed with status code 429',
      config: {
        url: '/auth/signup',
        data: JSON.stringify({ email: 'ann.lee@example.com', password: PASSWORD }),
      },
    };
    mockedPost.mockRejectedValue(failure);
    renderPage();

    await fillIn(user);
    await submit(user);
    expect(await screen.findByText(RATE_LIMITED_DETAIL)).toBeInTheDocument();

    const logged = consoleSpies.flatMap((spy) => spy.mock.calls.flat().map(stringify));
    expect(logged.filter((line) => line.includes(PASSWORD))).toEqual([]);
    consoleSpies.forEach((spy) => expect(spy).not.toHaveBeenCalledWith(failure));
  });
});
