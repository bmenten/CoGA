import React from 'react';
import { Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import type { ApiClinicalCnv } from '../lib/apiTypes';

/**
 * Renders a breadcrumb trail based on the current route.
 * Always begins with a link back to the dashboard and
 * skips rendering on auth pages.
 */
const Breadcrumbs: React.FC = () => {
  const { pathname, search } = useLocation();
  const segments = pathname.split('/').filter(Boolean);

  // On the clinical CNV detail route, show the CNV's name instead of its id.
  // Subscribe to the cache the detail page populates (no fetch of our own).
  const cnvDetailId = segments[0] === 'cnv-details' && segments[1] ? segments[1] : null;
  const { data: cnvCrumb } = useQuery<ApiClinicalCnv>({
    queryKey: ['clinical-cnv', cnvDetailId],
    enabled: false,
    queryFn: () => {
      throw new Error('disabled');
    },
  });

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
  const isAdminPath = segments[0] === 'admin';
  let path = '';
  others.forEach((segment, index) => {
    path += `/${segment}`;
    let label = segment
      .split('-')
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(' ')
      .toUpperCase();
    if (segment === 'cnv-details') {
      label = 'CNV EXPLORER';
    } else if (segment === cnvDetailId && cnvCrumb?.label) {
      label = cnvCrumb.label.toUpperCase();
    }
    const isLast = index === others.length - 1;
    let to = path;

    if (segment === 'admin') {
      to = '/admin';
    }

    if (segment === 'cnv-details') {
      to = '/cnv-explorer';
    }

    if (isAdminPath) {
      if (path === '/admin/access' || path === '/admin/reference' || path === '/admin/operations' || path === '/admin/variants' || path === '/admin/monitoring') {
        to = '/admin';
      }
      if (path.startsWith('/admin/data/families/') && segment !== 'structure') {
        to = '/admin/data/families';
      }
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
