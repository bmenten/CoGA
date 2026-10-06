import React from 'react';
import LoadingBar from '../../components/LoadingBar';

interface VariantResultsLoadingProps {
  /** What is loading, as the card's title ("Loading variants"). */
  title: string;
  /** One line on what the wait is for. */
  message: string;
}

/**
 * The loader a variant page shows over its results while a search is in flight: a bar along
 * the top of the results, a card in the middle of the screen saying what is loading, and a
 * veil over the results underneath (fade them with `variant-results-fetching`). Render it
 * inside `.variant-results-region`.
 */
const VariantResultsLoading: React.FC<VariantResultsLoadingProps> = ({ title, message }) => (
  <>
    <LoadingBar label={title} />
    <div className="variant-results-overlay" aria-hidden="true" />
    <div className="variant-results-loading-card" role="status" aria-live="polite" aria-busy="true">
      <span className="viz-loading-spinner viz-loading-spinner--lg" aria-hidden="true" />
      <div className="variant-results-overlay-text">
        <span className="variant-results-overlay-title">{title}…</span>
        <span className="variant-results-overlay-sub">{message}</span>
      </div>
    </div>
  </>
);

export default VariantResultsLoading;
