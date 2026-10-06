import React from 'react';
import { Link, useLocation } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import type { ApiClinicalCnv } from '../lib/apiTypes';

/** Words a route spells in lower case that read as acronyms or brand names. */
const ROUTE_WORDS: Record<string, string> = {
  acmg: 'ACMG',
  clickhouse: 'ClickHouse',
  cnv: 'CNV',
  dna: 'DNA',
  hpo: 'HPO',
  igv: 'IGV',
  nipt: 'NIPT',
  qc: 'QC',
  roi: 'ROI',
  sv: 'SV',
};

/** The segments whose next segment is an identifier (a family, chromosome, panel or CNV). */
const ID_PARENTS = new Set(['families', 'chromosome', 'panels', 'cnv-details']);

/** A route word in sentence case: `small-variants` reads "Small variants". */
const routeLabel = (segment: string): string => {
  const words = segment.split('-').map((word) => ROUTE_WORDS[word] ?? word);
  const label = words.join(' ');
  return label.charAt(0).toUpperCase() + label.slice(1);
};

/** An identifier as it is stored: case is part of it ("1q21.1", "demo_family"). */
const idLabel = (segment: string): string => {
  try {
    return decodeURIComponent(segment);
  } catch {
    return segment;
  }
};

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
      Dashboard
    </Link>,
  ];

  const others = segments.filter((s) => s !== 'dashboard');
  const isAdminPath = segments[0] === 'admin';
  let path = '';
  others.forEach((segment, index) => {
    path += `/${segment}`;
    const isId = index > 0 && ID_PARENTS.has(others[index - 1]);
    let label = isId ? idLabel(segment) : routeLabel(segment);
    if (segment === 'cnv-details') {
      label = 'CNV explorer';
    } else if (segment === cnvDetailId && cnvCrumb?.label) {
      label = cnvCrumb.label;
    }
    const isLast = index === others.length - 1;
    let to = path;

    if (segment === 'admin') {
      to = '/admin';
    }

    if (segment === 'cnv-details') {
      to = '/cnv-explorer';
    }

    // Families are listed on the dashboard: there is no /families page of its own.
    if (path === '/families') {
      to = '/dashboard';
    }

    // The reference docs are reached from the user guide: there is no /docs/reference page.
    if (path === '/docs/reference') {
      to = '/docs';
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
