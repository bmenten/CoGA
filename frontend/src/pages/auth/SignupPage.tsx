import React, { useEffect, useState } from 'react';
import { Link } from 'react-router';
import api from '../../lib/api';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';
import { fetchSignInConfig } from '../../lib/microsoftSignIn';

// The backend's minimum (UserCreate, NIST SP 800-63B-4): checked here first, so the form
// says so before a request is made.
const PASSWORD_MIN_LENGTH = 15;

const SignupPage: React.FC = () => {
  const [firstName, setFirstName] = useState('');
  const [lastName, setLastName] = useState('');
  const [email, setEmail] = useState('');
  const [affiliation, setAffiliation] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [submitting, setSubmitting] = useState(false);
  // The server's acknowledgement. The account waits for an administrator, and the page
  // says so: it used to go straight to the login page, where signing in then failed as
  // "User not active" with no word of why (#526).
  const [acknowledgement, setAcknowledgement] = useState<string | null>(null);
  // With Microsoft sign-in there is no sign-up: the first sign-in makes the account (SEC-4).
  const [microsoftSignIn, setMicrosoftSignIn] = useState(false);

  useEffect(() => {
    let current = true;
    fetchSignInConfig()
      .then((config) => {
        if (current) setMicrosoftSignIn(Boolean(config.microsoft));
      })
      .catch(() => undefined);
    return () => {
      current = false;
    };
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (submitting) return;
    setError('');
    setSubmitting(true);
    try {
      const response = await api.post('/auth/signup', {
        email,
        password,
        first_name: firstName,
        last_name: lastName,
        affiliation,
      });
      setPassword('');
      setAcknowledgement(
        (response.data as { detail?: string } | undefined)?.detail ||
          'Registration received. An administrator will review the request.',
      );
    } catch (err: unknown) {
      const message = getErrorMessage(err, 'Sign up failed', {
        networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
      });
      if (import.meta.env.DEV && import.meta.env.MODE !== 'test') {
        // The message only: the error object carries the request, password included.
        console.error('Sign up failed:', message);
      }
      setError(message);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-form">
          <div className="auth-form-intro">
            <h1>Create account</h1>
            <p className="page-subtitle">
              Use your professional details so administrators can assign the right access.
            </p>
          </div>
          {microsoftSignIn ? (
            <p className="status-note status-note--info" role="status">
              There is no sign-up: your first sign-in with the Microsoft account of your hospital or
              university makes your CoGA account. An administrator then gives you access to your
              projects.
            </p>
          ) : acknowledgement ? (
            <div className="space-y-4" role="status">
              <p className="status-note status-note--success">{acknowledgement}</p>
              <p className="page-subtitle">
                You can sign in once your account has been activated.
              </p>
            </div>
          ) : (
          <form onSubmit={handleSubmit} className="field-grid">
            <label className="field-label">
              First name
              <input
                value={firstName}
                onChange={(e) => setFirstName(e.target.value)}
                placeholder="First name"
                autoComplete="given-name"
                required
              />
            </label>
            <label className="field-label">
              Last name
              <input
                value={lastName}
                onChange={(e) => setLastName(e.target.value)}
                placeholder="Last name"
                autoComplete="family-name"
                required
              />
            </label>
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
              Affiliation
              <input
                value={affiliation}
                onChange={(e) => setAffiliation(e.target.value)}
                placeholder="Affiliation"
                autoComplete="organization"
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
                autoComplete="new-password"
                minLength={PASSWORD_MIN_LENGTH}
                required
                aria-describedby="signup-password-rule"
              />
            </label>
            <p id="signup-password-rule" className="table-subtle">
              At least {PASSWORD_MIN_LENGTH} characters. A passphrase of several words works well.
            </p>
            <button
              type="submit"
              className="form-button"
              disabled={submitting}
            >
              {submitting ? 'Signing up…' : 'Sign up'}
            </button>
            {error && (
              <p className="status-note status-note--error text-center" aria-live="polite">
                {error}
              </p>
            )}
          </form>
          )}
          <p className="auth-form-switch">
            Already registered? <Link to="/login">Return to login</Link>
          </p>
        </div>
      </div>
    </div>
  );
};

export default SignupPage;
