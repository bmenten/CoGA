import React from 'react';

interface Props {
  /** What the track failed to load, e.g. "small variants". */
  what: string;
  onRetry?: () => void;
}

/**
 * Shown when a track's request failed. It must never read like an empty result: a
 * failure is not "no variants in this region" (#510).
 */
const VizErrorOverlay: React.FC<Props> = ({ what, onRetry }) => (
  <div className="viz-error-overlay" role="alert">
    <span>Could not load {what} — this is not an empty result.</span>
    {onRetry ? (
      <button type="button" className="viz-error-retry" onClick={onRetry}>
        Retry
      </button>
    ) : null}
  </div>
);

export default VizErrorOverlay;
