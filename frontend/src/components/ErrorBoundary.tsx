import React from 'react';
import { Link } from 'react-router';
import PageState from './PageState';

type Props = {
  children: React.ReactNode;
  /**
   * Clears a caught error when it changes — the Layout passes the route, so navigating
   * away (e.g. via the "Dashboard" link on the error screen itself) shows the new view
   * instead of the same error screen (#510).
   */
  resetKey?: string;
};
type State = { hasError: boolean; message?: string };

export default class ErrorBoundary extends React.Component<Props, State> {
  constructor(props: Props) {
    super(props);
    this.state = { hasError: false };
  }

  static getDerivedStateFromError(error: unknown): State {
    const message = error instanceof Error ? error.message : String(error);
    return { hasError: true, message };
  }

  componentDidUpdate(prevProps: Props) {
    if (this.state.hasError && prevProps.resetKey !== this.props.resetKey) {
      this.setState({ hasError: false, message: undefined });
    }
  }

  componentDidCatch(error: unknown, errorInfo: unknown) {
     
    console.error('Render error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <PageState
          kicker="Error"
          title="This view could not be displayed"
          message="The page hit an unexpected error. You can retry the view or return to the dashboard."
          action={
            <>
              <button onClick={() => this.setState({ hasError: false, message: undefined })}>
                Try again
              </button>
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
