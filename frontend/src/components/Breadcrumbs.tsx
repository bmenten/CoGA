import React from 'react';
import { Link, useLocation } from 'react-router-dom';

/**
 * Renders a breadcrumb trail based on the current route.
 * Always begins with a link back to the dashboard and
 * skips rendering on auth pages.
 */
const Breadcrumbs: React.FC = () => {
  const { pathname, search } = useLocation();
  const segments = pathname.split('/').filter(Boolean);

  if (segments.length === 0 || ['login', 'signup'].includes(segments[0])) {
    return null;
  }

  const crumbs: React.ReactNode[] = [
    <Link
      key="/dashboard"
      to="/dashboard"
      className="subtle-link breadcrumb-link"
    >
      DASHBOARD
    </Link>,
  ];

  const others = segments.filter((s) => s !== 'dashboard');
  let path = '';
  others.forEach((segment, index) => {
    path += `/${segment}`;
    const label = (segment.charAt(0).toUpperCase() + segment.slice(1)).toUpperCase();
    const isLast = index === others.length - 1;
    let to = path;

    if (segment === 'admin') {
      to = '/dashboard';
    }

    if (segment === 'chromosome' && others[index - 1]) {
      const familyId = others[index - 1];
      const params = new URLSearchParams(search);
      params.delete('start');
      params.delete('end');
      params.delete('chr');
      params.delete('chrom');
      params.delete('chromosome');
      const query = params.toString();
      to = `/families/${familyId}/genome${query ? `?${query}` : ''}`;
    }

    const comingFromIgv = segments.includes('igv');
    const isFamiliesCrumb = segment === 'families';
    const requireHardReload = isFamiliesCrumb && comingFromIgv;
    crumbs.push(
      <React.Fragment key={path}>
        <span className="mx-2 breadcrumb-separator">/</span>
        {isLast ? (
          <span className="breadcrumb-current">{label}</span>
        ) : (
          <Link
            to={to}
            className="subtle-link breadcrumb-link"
            {...(requireHardReload ? { reloadDocument: true } : {})}
          >
            {label}
          </Link>
        )}
      </React.Fragment>
    );
  });

  return (
    <nav className="breadcrumb-shell text-sm">
      <div className="breadcrumb-inner">
        <div className="breadcrumb-trail flex flex-wrap items-center">{crumbs}</div>
      </div>
    </nav>
  );
};

export default Breadcrumbs;
