import React from 'react';

interface PageStateProps {
  kicker?: string;
  title: string;
  message?: string;
  action?: React.ReactNode;
  narrow?: boolean;
}

const PageState: React.FC<PageStateProps> = ({
  kicker = 'Status',
  title,
  message,
  action,
  narrow = false,
}) => (
  <div className={narrow ? 'page-shell-narrow' : 'page-shell'}>
    <div className="surface-card page-state">
      <div className="space-y-2">
        <p className="page-kicker">{kicker}</p>
        <h1 className="page-state-title">{title}</h1>
        {message ? <p className="page-state-copy">{message}</p> : null}
      </div>
      {action ? <div className="inline-actions justify-center">{action}</div> : null}
    </div>
  </div>
);

export default PageState;
