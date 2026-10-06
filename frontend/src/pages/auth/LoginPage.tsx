import React, { useState } from 'react';
import { Navigate, useNavigate, Link, useLocation } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import { isAuthenticated, persistSession } from '../../lib/auth';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';

const resolveNextPath = (rawNext: string | null): string => {
  if (!rawNext) return '/dashboard';
  if (!rawNext.startsWith('/') || rawNext.startsWith('//')) {
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
