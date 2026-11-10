import React, { Suspense, lazy, useEffect, useState } from 'react';
import { Link, Outlet, useLocation, useNavigate } from 'react-router';
import { useQueryClient } from '@tanstack/react-query';
import Breadcrumbs from './Breadcrumbs';
import ErrorBoundary from './ErrorBoundary';
import HeaderMenu from './HeaderMenu';
import ModalDialog from './ModalDialog';
import PageState from './PageState';
import { formatBuild, useAppVersion } from '../lib/appVersion';
import { endServerSession } from '../lib/api';
import {
  clearSession,
  getAuthToken,
  getSessionSignIn,
  getStoredUsername,
  onSessionEndedElsewhere,
} from '../lib/auth';
import { forgetMicrosoftAccount } from '../lib/microsoftSignIn';
import { DEVICE_MANUFACTURER, DEVICE_NAME, DEVICE_STATUS } from '../lib/deviceLabel';
import { githubRepositoryUrl } from '../lib/githubLinks';
import { currentProblemReportLink } from '../lib/problemReport';
import { useUiTelemetry } from '../lib/useUiTelemetry';
import { flushUiEventsNow } from '../lib/telemetry';

// React's own lazy, not lazyPage: settings open in a dialog over a page, which a reload would lose.
const SettingsPage = lazy(() => import('../pages/settings/SettingsPage'));

// The pages for a user without a session: the sign-in pages and the sign-up.
const SIGNED_OUT_PAGE = /^\/(login|signup)(\/|$)/;

const Layout: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();
  const username = getStoredUsername();
  const [showSettings, setShowSettings] = useState(false);
  const queryClient = useQueryClient();
  // The device label (TF-15 §1): the running build, and where to report a problem.
  const appVersion = useAppVersion();
  const problemReport = currentProblemReportLink();

  useUiTelemetry();

  const handleLogout = () => {
    // The logout click and anything still queued go out under this session first,
    // then nothing of this user's may stay behind for the next person at this tab:
    // the query cache holds their family data for up to ten minutes.
    const microsoft = getSessionSignIn() === 'microsoft';
    const token = getAuthToken();
    const flushed = flushUiEventsNow();
    queryClient.clear();
    clearSession();
    // And the server ends the session, once those events are answered: every token of the
    // account is refused from then on, on every device, a copy of this one included.
    void endServerSession(token, flushed);
    if (microsoft) {
      // And no Microsoft token either: MSAL could otherwise renew one without asking. The
      // sign-in page then offers to sign out of Microsoft too.
      void forgetMicrosoftAccount();
    }
    navigate('/login?logged_out=1', { replace: true });
  };

  // A logout in another tab of this browser ends the session in this tab too, as this tab's
  // own logout does: the tabs share the session, and this tab would otherwise go on showing the
  // user's data, with MSAL's tokens in the tab. So does a sign-in there of another account, which
  // replaces the session without a logout: this tab would otherwise go on showing the first
  // account's pages while its requests go out as the other. That account's session ends with it,
  // in every tab: no tab goes on as an account it does not show, and the account signs in again.
  // A sign-in page has no session to end.
  useEffect(
    () =>
      onSessionEndedElsewhere(() => {
        if (SIGNED_OUT_PAGE.test(location.pathname)) return;
        // The queued events are kept for the account they were made under, never sent under
        // another: the token is gone, or another account's.
        void flushUiEventsNow();
        queryClient.clear();
        clearSession();
        setShowSettings(false);
        // How the session signed in may have gone with the other tab's write: MSAL's account in
        // this tab is forgotten whichever it was, so it renews no token into a later session.
        void forgetMicrosoftAccount();
        navigate('/login?logged_out=1', { replace: true });
      }),
    [location.pathname, navigate, queryClient],
  );

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
              <span className="app-userpill app-header-control" title={username}>
                {username}
              </span>
              <button
                type="button"
                onClick={handleLogout}
                className="button-ghost app-header-control"
              >
                Logout
              </button>
              <HeaderMenu />
            </div>
          )}
        </div>
      </header>
      <Breadcrumbs />
      <main className="flex-1">
        <div className="app-main-inner">
          <ErrorBoundary resetKey={location.pathname}>
            <Outlet />
          </ErrorBoundary>
        </div>
      </main>
      <footer className="app-footer">
        <div className="app-footer-inner">
          <p className="app-footer-line">
            {DEVICE_NAME}
            {' · '}
            <span className="app-footer-version">
              {appVersion.isError
                ? 'Version unavailable'
                : appVersion.data
                  ? `Version ${formatBuild(appVersion.data.version, appVersion.data.git_sha)}`
                  : 'Version loading…'}
            </span>
          </p>
          <p className="app-footer-line">{DEVICE_STATUS}</p>
          <p className="app-footer-line">Manufacturer: {DEVICE_MANUFACTURER}</p>
          <p className="app-footer-line">
            <Link to="/new-features">New features</Link>
            {problemReport ? (
              <>
                {' · '}
                <a
                  href={problemReport.href}
                  {...(problemReport.newTab ? { target: '_blank', rel: 'noopener noreferrer' } : {})}
                >
                  {problemReport.label}
                </a>
              </>
            ) : null}
            {' · '}
            <a href={githubRepositoryUrl} target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
          </p>
        </div>
      </footer>
      {showSettings && (
        // A dialog: named, Escape closes it, focus stays inside.
        <ModalDialog
          label="User settings"
          confirmDiscard={false}
          onClose={() => setShowSettings(false)}
        >
            <button
              type="button"
              onClick={() => setShowSettings(false)}
              className="button-ghost absolute right-4 top-4 z-10"
            >
              Close
            </button>
            {/* Its own error screen: settings that cannot be loaded (CoGA updated since the tab
                was opened) leave the page beneath as it is. */}
            <ErrorBoundary>
              <Suspense
                fallback={
                  <PageState
                    loading
                    kicker="Preferences"
                    title="Loading settings"
                    message="Preparing your local viewer preferences."
                    narrow
                  />
                }
              >
                <SettingsPage />
              </Suspense>
            </ErrorBoundary>
        </ModalDialog>
      )}
    </div>
  );
};

export default Layout;
