import React from 'react';

interface Props {
  message?: string;
}

const VizLoadingOverlay: React.FC<Props> = ({ message = 'Loading data' }) => (
  <div className="viz-loading-overlay" aria-live="polite">
    <span className="viz-loading-spinner" />
    <span>{message}</span>
  </div>
);

export default VizLoadingOverlay;
