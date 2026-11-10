import React from 'react';
import { Link } from 'react-router';
import { isModuleLoadError, tab } from '../lib/lazyPage';
import PageState from './PageState';

type Props = {
  children: React.ReactNode;
  /**
   * Clears a caught error when it changes — the Layout passes the route, so navigating
   * away (e.g. via the "Dashboard" link on the error screen itself) shows the new view
   * instead of the same error screen.
   */
  resetKey?: string;
};
// `notLoaded`: a part of the app that could not be loaded, most often because CoGA was updated
// since the tab was opened. Retrying it fails the same way (React keeps the failed load), so the
// screen offers to reload the tab instead.
type State = { hasError: boolean; message?: string; notLoaded?: boolean };

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: unknown): State {
    const message = error instanceof Error ? error.message : String(error);
    return { hasError: true, message, notLoaded: isModuleLoadError(error) };
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, message: undefined, notLoaded: undefined });
    }
  }

  componentDidCatch(error: unknown, errorInfo: unknown) {
     
    console.error('Render error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      const { notLoaded } = this.state;
      return (
        <PageState
          kicker="Error"
          title={notLoaded ? 'This view could not be loaded' : 'This view could not be displayed'}
          message={
            notLoaded
              ? 'CoGA may have been updated since this page was opened. Reload the page to load the new version, or return to the dashboard.'
              : 'The page hit an unexpected error. You can retry the view or return to the dashboard.'
          }
          action={
            <>
              {notLoaded ? (
                <button onClick={() => tab.reload()}>Reload page</button>
              ) : (
                <button onClick={() => this.setState({ hasError: false, message: undefined })}>
                  Try again
                </button>
              )}
              <Link to="/dashboard" className="button-secondary">
                Dashboard
              </Link>
              {this.state.message ? (
                <details className="error-detail">
                  <summary>Technical detail</summary>
                  <pre>{this.state.message}</pre>
                </details>
              ) : null}
            </>
          }
          narrow
        />
      );
    }
    return this.props.children;
  }
}
