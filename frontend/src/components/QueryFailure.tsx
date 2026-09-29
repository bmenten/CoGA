import React from 'react';
import { getErrorMessage } from '../lib/errorMessage';

/**
 * A request that failed, said where its result would be shown: never as an empty result
 * (#510). The server's reason is given when it sent one, and what the failure means for
 * the page, when it is more than a missing section (#606).
 */
const QueryFailure: React.FC<{
  /** What could not be loaded, e.g. "the structural variants". */
  what: string;
  error?: unknown;
  onRetry?: () => void;
  /** What the failure means here, e.g. "The default Mendeliome scope is not applied." */
  consequence?: string;
}> = ({ what, error, onRetry, consequence }) => {
  const reason = error ? getErrorMessage(error, '') : '';
  return (
    <div className="variant-workspace-feedback variant-workspace-feedback--error" role="alert">
      Could not load {what} — this is not an empty result.
      {reason ? ` ${reason}` : ''}
      {consequence ? ` ${consequence}` : ''}{' '}
      {onRetry ? (
        <button type="button" className="button-link" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
};

export default QueryFailure;
