import React from 'react';

/**
 * The header card a page opens with: the signature-red kicker, the page title, a short summary
 * under it, and the page's own actions at the right (stacked under the copy on a narrow screen).
 * Whatever the page shows under its title row (a status note, a stat grid) goes in `children`.
 */
interface PageHeaderProps {
  kicker: React.ReactNode;
  title: React.ReactNode;
  /** The summary under the title. A string or inline content; it renders in one paragraph. */
  description?: React.ReactNode;
  actions?: React.ReactNode;
  /** Set when the title carries an id an in-page link jumps to. */
  titleId?: string;
  className?: string;
  children?: React.ReactNode;
}

const PageHeader: React.FC<PageHeaderProps> = ({
  kicker,
  title,
  description,
  actions,
  titleId,
  className,
  children,
}) => (
  <section className={['surface-card page-top-card', className].filter(Boolean).join(' ')}>
    <div className="page-header">
      <div className="page-header-copy">
        <p className="page-kicker">{kicker}</p>
        <h1 id={titleId} className="catalog-card-title">
          {title}
        </h1>
        {description ? <p className="catalog-card-copy">{description}</p> : null}
      </div>
      {actions ? <div className="page-header-actions">{actions}</div> : null}
    </div>
    {children ? <div className="page-top-card-body">{children}</div> : null}
  </section>
);

export default PageHeader;
