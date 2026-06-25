import React from 'react';

import LoadingBar from './LoadingBar';

interface PageStateProps {
  kicker?: string;
  title: string;
  message?: string;
  action?: React.ReactNode;
  narrow?: boolean;
  /** Render an animated loading bar + spinner (use for "loading…" states). */
  loading?: boolean;
}

const PageState: React.FC<PageStateProps> = ({
  kicker = 'Status',
  title,
  message,
  action,
  narrow = false,
  loading = false,
}) => (
  <div className={narrow ? 'page-shell-narrow' : 'page-shell'}>
    <div className={`surface-card page-state${loading ? ' page-state--loading' : ''}`}>
      {loading ? <LoadingBar label={title} /> : null}
      <div className="space-y-2">
        {loading ? (
          <span
            className="viz-loading-spinner viz-loading-spinner--lg page-state-spinner"
            aria-hidden="true"
          />
        ) : null}
        <p className="page-kicker">{kicker}</p>
        <h1 className="page-state-title">{title}</h1>
        {message ? <p className="page-state-copy">{message}</p> : null}
      </div>
      {action ? <div className="inline-actions justify-center">{action}</div> : null}
    </div>
  </div>
);

export default PageState;
