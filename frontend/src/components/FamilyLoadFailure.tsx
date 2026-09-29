import React from 'react';

import PageState from './PageState';
import { getErrorMessage, isNotFoundError } from '../lib/errorMessage';

/**
 * The page-level state for a family page whose family, or whose own data, did not load. The
 * API answers 404 when the family does not exist or is not visible to this user: that is
 * "Family not found", which these pages said for any failure. Any other failure is a request
 * that failed, said as one with the server's reason and a retry (#610).
 */
const FamilyLoadFailure: React.FC<{
  kicker: string;
  /** What could not be loaded, e.g. "Family" or "mtDNA analysis". */
  what: string;
  error: unknown;
  /** Said under "Family not found". */
  notFoundMessage: string;
  onRetry: () => void;
  /** Shown beside Retry, and alone when the family was not found. */
  action?: React.ReactNode;
}> = ({ kicker, what, error, notFoundMessage, onRetry, action }) => {
  if (isNotFoundError(error)) {
    return (
      <PageState kicker={kicker} title="Family not found" message={notFoundMessage} action={action} />
    );
  }
  return (
    <PageState
      kicker={kicker}
      title={`${what} could not be loaded`}
      message={`${getErrorMessage(error, 'The request failed.')} This is a failed request, not a missing family.`}
      action={
        <>
          <button type="button" className="button-secondary" onClick={onRetry}>
            Retry
          </button>
          {action}
        </>
      }
    />
  );
};

export default FamilyLoadFailure;
