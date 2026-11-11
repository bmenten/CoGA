import React from 'react';
import QueryFailure from '../../components/QueryFailure';

/**
 * A variant-summary section whose figures are not in: loading, or failed with the reason and
 * a retry. Each section loads on its own, so one that fails leaves the others shown, and a
 * failure never reads as an empty result.
 */
export const VariantSummarySectionState: React.FC<{
  /** What the section loads, e.g. "small-variant statistics". */
  what: string;
  loading: boolean;
  error: unknown;
  onRetry: () => void;
}> = ({ what, loading, error, onRetry }) =>
  error ? (
    <QueryFailure what={`the ${what}`} error={error} onRetry={onRetry} />
  ) : loading ? (
    <p className="analysis-count" role="status">
      Loading the {what}…
    </p>
  ) : null;
