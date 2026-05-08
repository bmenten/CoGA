import React from 'react';
import PageState from './PageState';

type Props = { children: React.ReactNode };
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

  componentDidCatch(error: unknown, errorInfo: unknown) {
    // eslint-disable-next-line no-console
    console.error('Render error:', error, errorInfo);
  }

  render() {
    if (this.state.hasError) {
      return (
        <PageState
          kicker="Error"
          title="Something went wrong."
          message={this.state.message}
          action={
            <button onClick={() => this.setState({ hasError: false, message: undefined })}>
              Try again
            </button>
          }
          narrow
        />
      );
    }
    return this.props.children;
  }
}
