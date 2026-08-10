import React from 'react';
import { Link } from 'react-router';

/**
 * A link from a variant list into a genome workspace — IGV, chromosome view, the
 * genome overview, Circos.
 *
 * These always open in a new tab. The small-variant and structural-variant tables
 * sit behind an expensive, heavily filtered query, so following a locus in the same
 * tab costs a full re-run of that query (plus the filter state and scroll position)
 * every time a reviewer wants to look at one variant. Opening a tab leaves the
 * working list untouched underneath, which is the whole point of the control.
 *
 * `label` describes the destination and is spelled out into both the tooltip and an
 * explicit `aria-label`, so the new-tab behaviour is announced instead of being a
 * surprise — and so the accessible name never depends on how the visible chips
 * happen to be laid out (see the User Guide workspace links for the same reasoning).
 */
const GenomeWorkspaceLink: React.FC<{
  to: string;
  label: string;
  className?: string;
  children: React.ReactNode;
}> = ({ to, label, className, children }) => {
  const announced = `${label} (opens in a new tab)`;
  return (
    <Link
      to={to}
      className={className}
      target="_blank"
      rel="noopener noreferrer"
      title={announced}
      aria-label={announced}
    >
      {children}
    </Link>
  );
};

export default GenomeWorkspaceLink;
