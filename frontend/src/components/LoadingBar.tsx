import React from 'react';

type LoadingBarProps = {
  /** Accessible label announced while the bar is shown. */
  label?: string;
};

/**
 * A thin, indeterminate animated progress bar — a sliding segment that runs while an
 * unbounded request is in flight (e.g. a variant search). Pin it to the top of the
 * container it reports on; it carries its own status role for screen readers.
 */
const LoadingBar: React.FC<LoadingBarProps> = ({ label = 'Loading…' }) => (
  <div className="loading-bar" role="status" aria-live="polite" aria-label={label}>
    <span className="loading-bar-track">
      <span className="loading-bar-flyer" aria-hidden="true" />
    </span>
  </div>
);

export default LoadingBar;
