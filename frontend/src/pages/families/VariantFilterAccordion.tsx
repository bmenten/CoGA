// The filter categories of the variant pages (small variants, structural variants, the
// monogenic NIPT list and the small-variant explorer): one row per category, in a single
// column across the page, behind an "Advanced filters" toggle.
import { useId, useState } from 'react';
import type { ReactNode, SyntheticEvent } from 'react';
import { stopSummaryInteraction } from './variantFilterFormUtils';

/**
 * One category of the filter accordion: its title, a one-line summary of what is set, any
 * quick controls at the right of its bar, and the category's own fields below. `active`
 * marks a category with a filter set, so a closed row still shows where the filters are.
 */
export const FilterSection = ({
  title,
  meta,
  active = false,
  open,
  onToggle,
  controls,
  children,
}: {
  title: string;
  meta: ReactNode;
  active?: boolean;
  open: boolean;
  onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => void;
  controls?: ReactNode;
  children: ReactNode;
}) => (
  <details
    className={`variant-filter-dropdown${active ? ' variant-filter-dropdown--active' : ''}`}
    open={open}
    onToggle={onToggle}
  >
    <summary className="variant-filter-dropdown-summary">
      <span className="variant-filter-dropdown-caret" aria-hidden="true">
        ▾
      </span>
      <span className="variant-filter-dropdown-summary-copy">
        <span className="variant-filter-dropdown-title">{title}</span>
        <span className="variant-filter-dropdown-meta">{meta}</span>
      </span>
      {controls !== undefined ? (
        <span
          className="variant-filter-dropdown-summary-controls"
          // Only keeps a click on a control from also toggling the <summary>.
          role="presentation"
          onMouseDown={stopSummaryInteraction}
          onClick={stopSummaryInteraction}
        >
          {controls}
        </span>
      ) : null}
    </summary>
    {children}
  </details>
);

/**
 * The filter categories, one row each. `collapsible`: behind an "Advanced filters" toggle,
 * collapsed on open, which still says how many categories there are and how many have a
 * filter set. The rows stay mounted while collapsed, so collapsing never changes a filter.
 * Shown, "Expand all" and "Collapse all" open or close every row.
 */
export const FilterAccordion = ({
  collapsible,
  categoryCount,
  activeCategoryCount,
  onExpandAll,
  onCollapseAll,
  children,
}: {
  collapsible: boolean;
  categoryCount: number;
  activeCategoryCount: number;
  onExpandAll: () => void;
  onCollapseAll: () => void;
  children: ReactNode;
}) => {
  const [shown, setShown] = useState(!collapsible);
  const panelId = useId();
  const visible = !collapsible || shown;
  return (
    <section className="variant-search-section">
      <div className="variant-filter-accordion-bar">
        {collapsible ? (
          <button
            type="button"
            className="variant-advanced-toggle"
            aria-expanded={shown}
            aria-controls={panelId}
            onClick={() => setShown((current) => !current)}
          >
            <span className="variant-filter-dropdown-caret" aria-hidden="true">
              ▾
            </span>
            <span className="variant-advanced-toggle-title">Advanced filters</span>
            <span className="variant-advanced-toggle-meta">
              ({categoryCount} categories
              {activeCategoryCount > 0 ? `, ${activeCategoryCount} with filters set` : ''})
            </span>
          </button>
        ) : null}
        {visible ? (
          <span className="variant-filter-accordion-actions">
            <button type="button" className="button-link" onClick={onExpandAll}>
              Expand all
            </button>
            <span aria-hidden="true">|</span>
            <button type="button" className="button-link" onClick={onCollapseAll}>
              Collapse all
            </button>
          </span>
        ) : null}
      </div>
      {/* Hidden, not unmounted: collapsing keeps every field and its value as it is. */}
      <div id={panelId} className="variant-filter-dropdown-grid" hidden={!visible}>
        {children}
      </div>
    </section>
  );
};
