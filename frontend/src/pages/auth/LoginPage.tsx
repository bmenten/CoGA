import React, { useState } from 'react';
import { Navigate, useNavigate, Link } from 'react-router-dom';
import api from '../../lib/api';
import { isAuthenticated, persistSession } from '../../lib/auth';
import { buildApiUnavailableMessage, getErrorMessage } from '../../lib/errorMessage';
import circosPlotUrl from '../../assets/CIRCOS.jpg';
import haplotypeUrl from '../../assets/haplotypes.jpg';

const LoginPage: React.FC = () => {
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  if (isAuthenticated()) {
    return <Navigate to="/dashboard" replace />;
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
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
      persistSession(accessToken, me.data.email ?? email, me.data.role);
      navigate('/dashboard');
    } catch (err: unknown) {
      if (import.meta.env.DEV && import.meta.env.MODE !== 'test') {
        console.error(err);
      }
      setError(
        getErrorMessage(err, 'Login failed', {
          networkFallback: buildApiUnavailableMessage(api.defaults.baseURL),
        })
      );
    }
  };

  return (
    <div className="auth-shell">
      <div className="auth-card">
        <div className="auth-gallery">
          <div className="auth-gallery-inner">
            <div className="space-y-8">
              <div>
                <p className="page-kicker !text-[rgba(255,255,255,0.62)]">CoGA</p>
                <h2 className="auth-gallery-title">Comprehensive Genomic Analysis</h2>
              </div>

              <div className="auth-gallery-metrics">
                <div className="auth-gallery-metric">
                  <span className="auth-gallery-metric-label">Pedigree intake</span>
                  <span className="auth-gallery-metric-value">
                    Manual family builder plus PED ingestion with a live pedigree sketch.
                  </span>
                </div>
                <div className="auth-gallery-metric">
                  <span className="auth-gallery-metric-label">Genome review</span>
                  <span className="auth-gallery-metric-value">
                    Structural variants, small variants, chromosome context, and IGV from one
                    consistent shell.
                  </span>
                </div>
              </div>
            </div>

            <div className="auth-gallery-grid">
              <figure className="auth-gallery-figure">
                <img
                  src={circosPlotUrl}
                  alt="circos plot preview"
                  width={960}
                  height={748}
                  decoding="async"
                />
                <figcaption>Interactive genome summaries</figcaption>
              </figure>
              <figure className="auth-gallery-figure">
                <img
                  src={haplotypeUrl}
                  alt="family haplotyping preview"
                  width={760}
                  height={452}
                  decoding="async"
                />
                <figcaption>Haplotype review in family context</figcaption>
              </figure>
            </div>
          </div>
        </div>
        <div className="auth-form">
          <div className="auth-form-shell">
            <div className="auth-form-intro">
              <p className="auth-form-note">CoGA</p>
              <p className="page-kicker">Sign In</p>
              <p className="page-subtitle">
                Continue to the family workspace with your institutional account.
              </p>
            </div>

            <form onSubmit={handleSubmit} className="field-grid">
              <label className="field-label">
                Email
                <input
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  placeholder="Email"
                />
              </label>
              <label className="field-label">
                Password
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  placeholder="Password"
                />
              </label>
              <button type="submit" className="form-button w-full justify-center">
                Login
              </button>
              {error && (
                <p className="status-note status-note--error text-center" aria-live="polite">
                  {error}
                </p>
              )}
            </form>

            <p className="auth-form-note">Secure access via JWT session</p>
            <p className="mt-6 text-sm text-[var(--color-text-muted)]">
              Don&apos;t have an account?{' '}
              <Link to="/signup" className="subtle-link !inline-flex">
                Sign up
              </Link>
            </p>
          </div>
        </div>
      </div>
    </div>
  );
};

export default LoginPage;
