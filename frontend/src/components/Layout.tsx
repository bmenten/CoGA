import React, { Suspense, lazy, useState } from 'react';
import { Link, Outlet, useNavigate } from 'react-router-dom';
import Breadcrumbs from './Breadcrumbs';
import ErrorBoundary from './ErrorBoundary';
import PageState from './PageState';
import { clearSession, getStoredUsername } from '../lib/auth';
import { githubIssuesUrl, githubRepositoryUrl } from '../lib/githubLinks';

const SettingsPage = lazy(() => import('../pages/settings/SettingsPage'));

const Layout: React.FC = () => {
  const navigate = useNavigate();
  const username = getStoredUsername();
  const [showSettings, setShowSettings] = useState(false);

  const handleLogout = () => {
    clearSession();
    navigate('/login');
  };

  return (
    <div className="app-shell flex min-h-screen flex-col">
      <header className="app-header">
        <div className="app-header-inner">
          <Link to="/dashboard" className="app-brand">
            <span className="app-brand-mark" aria-hidden="true">
              <svg viewBox="0 0 64 64" className="app-brand-logo">
                <path
                  className="app-brand-logo-line"
                  d="M20.5 8C20.5 17.5 24.8 22.8 31.2 28.2C37.7 33.7 41.5 38.5 41.5 46.3C41.5 53 38.4 58 34.8 61"
                />
                <path
                  className="app-brand-logo-line"
                  d="M43.5 8C43.5 17.5 39.2 22.8 32.8 28.2C26.3 33.7 22.5 38.5 22.5 46.3C22.5 53 25.6 58 29.2 61"
                />
                <path
                  className="app-brand-logo-line"
                  d="M24.5 13.5H39.5"
                />
                <path
                  className="app-brand-logo-line"
                  d="M27 17.5H36"
                />
                <path
                  className="app-brand-logo-line"
                  d="M28.2 31.3H35.8"
                />
                <path
                  className="app-brand-logo-line"
                  d="M26.4 35.1H38"
                />
                <path
                  className="app-brand-logo-line"
                  d="M27 39H36.4"
                />
                <path
                  className="app-brand-logo-line"
                  d="M27.4 52.4H36.2"
                />
                <path
                  className="app-brand-logo-line"
                  d="M25 56.2H39"
                />
              </svg>
            </span>
            <div className="app-brand-copy">
              <span className="app-brand-title">CoGA</span>
              <span className="app-brand-subtitle">Comprehensive Genomic Analysis</span>
            </div>
          </Link>
          {username && (
            <div className="app-userbar">
              <button
                type="button"
                onClick={() => setShowSettings(true)}
                className="button-secondary app-header-control"
              >
                Settings
              </button>
              <span className="app-userpill app-header-control">{username}</span>
              <button
                type="button"
                onClick={handleLogout}
                className="button-ghost app-header-control"
              >
                Logout
              </button>
            </div>
          )}
        </div>
      </header>
      <Breadcrumbs />
      <main className="flex-1">
        <div className="app-main-inner">
          <ErrorBoundary>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
      <footer className="app-footer">
        <div className="app-footer-inner">
          CoGA, Comprehensive Genomic Analysis
          {' · '}
          Center for Medical Genetics, Ghent University
          {' · '}
          <Link to="/new-features">New features</Link>
          {' · '}
          <a href={githubIssuesUrl} target="_blank" rel="noopener noreferrer">
            Submit issue / request
          </a>
          {' · '}
          <a href={githubRepositoryUrl} target="_blank" rel="noopener noreferrer">
            GitHub
          </a>
          {' · '}
          <a href="mailto:bjorn.menten@ugent.be">Contact</a>
        </div>
      </footer>
      {showSettings && (
        <div className="modal-backdrop">
          <div className="modal-surface surface-card">
            <button
              type="button"
              onClick={() => setShowSettings(false)}
              className="button-ghost absolute right-4 top-4 z-10"
            >
              Close
            </button>
            <Suspense fallback={<PageState kicker="Preferences" title="Loading settings" message="Preparing your local viewer preferences." narrow />}>
              <SettingsPage />
            </Suspense>
          </div>
        </div>
      )}
    </div>
  );
};

export default Layout;
