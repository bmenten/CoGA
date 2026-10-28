import React, { useEffect, useState } from 'react';
import { Navigate, useNavigate, Link, useLocation } from 'react-router';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import api from '../../lib/api';
import AuthIntro from './AuthIntro';
import { isAuthenticated, persistSession } from '../../lib/auth';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';
import {
  completeMicrosoftSignIn,
  forgetMicrosoftAccount,
  signInConfigQuery,
  signOutOfMicrosoft,
  startMicrosoftSignIn,
} from '../../lib/microsoftSignIn';

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

interface LoginPageProps {
  // The emergency administrator's password sign-in beside Microsoft sign-in (/login/emergency,
  // SEC-4). Without Microsoft sign-in the password sign-in is /login itself.
  emergency?: boolean;
}

const LoginPage: React.FC<LoginPageProps> = ({ emergency = false }) => {
  const location = useLocation();
  const query = new URLSearchParams(location.search);
  const nextPath = resolveNextPath(query.get('next'));
  const loggedOut = query.get('logged_out') === '1';
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);
  // A Microsoft sign-in under way: on its way to Microsoft, or back with a token to check.
  const [microsoftStep, setMicrosoftStep] = useState<'idle' | 'leaving' | 'completing'>('idle');
  // Where a completed Microsoft sign-in goes: the page asked for before it, which the URL
  // Microsoft returned to no longer names.
  const [destination, setDestination] = useState<string | null>(null);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const signInConfig = useQuery(signInConfigQuery);
  const microsoft = signInConfig.data?.microsoft ?? null;
  const signedIn = isAuthenticated();

  // Microsoft sends the browser back here with the sign-in: complete it, then check the token
  // with the API and start the session as for a password sign-in.
  useEffect(() => {
    if (!microsoft || emergency || signedIn) {
      return undefined;
    }
    let current = true;
    completeMicrosoftSignIn(microsoft)
      .then(async (completed) => {
        if (!completed || !current) return;
        setMicrosoftStep('completing');
        const me = await api.get('/auth/me', {
          headers: { Authorization: `Bearer ${completed.accessToken}` },
        });
        if (!current) return;
        // Start the session with an empty cache: nothing loaded under an earlier session
        // in this tab may be shown to this user (#521).
        queryClient.clear();
        persistSession(completed.accessToken, me.data.email, me.data.role, 'microsoft');
        setDestination(resolveNextPath(completed.nextPath));
      })
      .catch(async (err: unknown) => {
        if (!current) return;
        // A refused account (not active, or another account has its address) is not kept
        // signed in with Microsoft in this tab.
        await forgetMicrosoftAccount();
        setMicrosoftStep('idle');
        setError(
          getErrorMessage(err, 'The Microsoft sign-in did not complete.', {
            networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
          }),
        );
      });
    return () => {
      current = false;
    };
  }, [microsoft, emergency, signedIn, queryClient]);

  if (destination) {
    return <Navigate to={destination} replace />;
  }
  if (signedIn) {
    return <Navigate to={nextPath} replace />;
  }
  if (emergency && signInConfig.isSuccess && !microsoft) {
    // Without Microsoft sign-in every account signs in with its password at /login.
    return <Navigate to="/login" replace />;
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
      persistSession(accessToken, me.data.email ?? email, me.data.role, 'password');
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

  const handleMicrosoftSignIn = async () => {
    if (!microsoft) return;
    setError('');
    setMicrosoftStep('leaving');
    try {
      await startMicrosoftSignIn(microsoft, nextPath);
    } catch (err: unknown) {
      setMicrosoftStep('idle');
      setError(getErrorMessage(err, 'The Microsoft sign-in could not be opened.'));
    }
  };

  const handleMicrosoftSignOut = async () => {
    if (!microsoft) return;
    setError('');
    try {
      await signOutOfMicrosoft(microsoft);
    } catch (err: unknown) {
      setError(getErrorMessage(err, 'The Microsoft sign-out could not be opened.'));
    }
  };

  const usesMicrosoft = Boolean(microsoft) && !emergency;
  const usesPassword = signInConfig.isSuccess && !usesMicrosoft;

  const errorNote = error ? (
    <p className="status-note status-note--error text-center" aria-live="polite">
      {error}
    </p>
  ) : null;

  const passwordForm = (
    <form onSubmit={handleSubmit} className="field-grid">
      <label className="field-label">
        Email
        <input
          type="email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="Email"
          autoComplete={emergency ? 'off' : 'email'}
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
          autoComplete={emergency ? 'off' : 'current-password'}
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
      {errorNote}
    </form>
  );

  return (
    <div className="auth-shell">
      <div className="auth-layout">
        <AuthIntro />
        <div className="auth-card">
          <div className="auth-form">
            <div className="auth-form-intro">
              <h1>{emergency ? 'Emergency sign-in' : 'Sign in'}</h1>
              <p className="page-subtitle">
                {emergency
                  ? 'For the emergency administrator only, when the Microsoft sign-in cannot be used. Every sign-in here, and everything done with it, is recorded in the audit log.'
                  : usesMicrosoft
                    ? 'Continue to the family workspace with the Microsoft account of your hospital or university.'
                    : 'Continue to the family workspace with your user account.'}
              </p>
              {loggedOut && (
                <p className="status-note status-note--success auth-inline-status" role="status">
                  {usesMicrosoft ? 'You have been signed out of CoGA.' : 'You have been signed out.'}
                </p>
              )}
              {nextPath !== '/dashboard' && !loggedOut && (
                <p className="status-note status-note--info auth-inline-status" role="status">
                  Sign in to continue to your requested page.
                </p>
              )}
            </div>

            {signInConfig.isPending && (
              <p className="status-note status-note--info" role="status">
                Loading the sign-in...
              </p>
            )}
            {signInConfig.isError && (
              <div className="field-grid">
                <p className="status-note status-note--error" role="alert">
                  {`The sign-in could not be loaded: ${getErrorMessage(signInConfig.error, 'the request failed', {
                    networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
                  })}`}
                </p>
                <button type="button" className="button-secondary" onClick={() => void signInConfig.refetch()}>
                  Try again
                </button>
              </div>
            )}

            {usesMicrosoft && (
              <div className="field-grid">
                {microsoftStep === 'completing' ? (
                  <p className="status-note status-note--info" role="status">
                    Completing the Microsoft sign-in...
                  </p>
                ) : (
                  <button
                    type="button"
                    className="form-button"
                    onClick={() => void handleMicrosoftSignIn()}
                    disabled={microsoftStep !== 'idle'}
                    aria-busy={microsoftStep === 'leaving' ? 'true' : 'false'}
                  >
                    {microsoftStep === 'leaving' ? 'Opening the Microsoft sign-in...' : 'Sign in with Microsoft'}
                  </button>
                )}
                {errorNote}
                {loggedOut && (
                  <>
                    <p className="auth-form-note">
                      Your Microsoft account may still be signed in in this browser. On a shared computer,
                      sign out of it too.
                    </p>
                    <button type="button" className="button-secondary" onClick={() => void handleMicrosoftSignOut()}>
                      Sign out of Microsoft
                    </button>
                  </>
                )}
              </div>
            )}

            {usesPassword && passwordForm}

            {usesMicrosoft && (
              <p className="auth-form-switch">
                Your first sign-in makes your CoGA account. An administrator then gives you access to
                your projects.
              </p>
            )}
            {usesPassword && emergency && (
              <p className="auth-form-switch">
                <Link to="/login">Back to the Microsoft sign-in</Link>
              </p>
            )}
            {usesPassword && !emergency && (
              <>
                <p className="auth-form-note">Secure access via JWT session</p>
                <p className="auth-form-switch">
                  Don&apos;t have an account? <Link to="/signup">Sign up</Link>
                </p>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
