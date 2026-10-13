import React, { useState } from 'react';
import { Navigate, useNavigate, Link, useLocation } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isAuthenticated, persistSession } from '../../lib/auth';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';

// The only `next` a sign-in follows (REQ-UI-006): a page of this site in the form that
// RequireAuth and RequireAdmin write, the page's path, query string and hash as the
// browser holds them, percent-encoded. That is a "/", a letter or a digit, and then
// printable ASCII only (! to ~) without a backslash. A browser reads such a value as a
// path on this site: it holds no character a browser drops (a tab, line feed or carriage
// return) or reads as a slash (a backslash), and once a letter or digit follows the first
// "/", nothing after it can name a host (UI-2). Any other `next` opens the dashboard, as
// an absent one does: a scheme, "//", a backslash, whitespace or a control character, and
// a path that starts with a percent-encoded character, such as "/%09/" or "/%2F/".
const SAME_SITE_PATH = /^\/[A-Za-z0-9][\x21-\x5B\x5D-\x7E]*$/;

const resolveNextPath = (rawNext: string | null): string => {
  if (!rawNext || !SAME_SITE_PATH.test(rawNext)) {
    return '/dashboard';
  }
  if (rawNext.startsWith('/login') || rawNext.startsWith('/signup')) {
    return '/dashboard';
  }
  return rawNext;
};

const LoginPage: React.FC = () => {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const nextPath = resolveNextPath(query.get('next'));
  const loggedOut = query.get('logged_out') === '1';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  if (isAuthenticated()) {
    return <Navigate to={nextPath} replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setIsSubmitting(true);
    try {
      const response = await api.post('/auth/login', {
        email,
        password,
      });
      const accessToken = response.data.access_token;
      const me = await api.get('/auth/me', {
        headers: {
          Authorization: `Bearer ${accessToken}`,
        },
      });
      // Start the session with an empty cache: nothing loaded under an earlier session
      // in this tab may be shown to this user (#521).
      queryClient.clear();
      persistSession(accessToken, me.data.email ?? email, me.data.role);
      navigate(nextPath, { replace: true });
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Login failed', {
        networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
      });
      if (import.meta.env.DEV && import.meta.env.MODE !== 'test') {
        // The message only: the error object carries the request, password included (#526).
        console.error('Login failed:', message);
      }
      setError(message);
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-form">
          <div className="auth-form-intro">
            <h1>Sign in</h1>
            <p className="page-subtitle">
              Continue to the family workspace with your user account.
            </p>
            {loggedOut && (
              <p className="status-note status-note--success auth-inline-status" role="status">
                You have been signed out.
              </p>
            )}
            {nextPath !== '/dashboard' && !loggedOut && (
              <p className="status-note status-note--info auth-inline-status" role="status">
                Sign in to continue to your requested page.
              </p>
            )}
          </div>

          <form onSubmit={handleSubmit} className="field-grid">
            <label className="field-label">
              Email
              <input
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                placeholder="Email"
                autoComplete="email"
                required
              />
            </label>
            <label className="field-label">
              Password
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder="Password"
                autoComplete="current-password"
                required
              />
            </label>
            <button
              type="submit"
              className="form-button"
              disabled={isSubmitting}
              aria-busy={isSubmitting ? 'true' : 'false'}
            >
              {isSubmitting ? 'Signing in...' : 'Login'}
            </button>
            {error && (
              <p className="status-note status-note--error text-center" aria-live="polite">
                {error}
              </p>
            )}
          </form>

          <p className="auth-form-note">Secure access via JWT session</p>
          <p className="auth-form-switch">
            Don&apos;t have an account? <Link to="/signup">Sign up</Link>
          </p>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
