/** The bar above a variant page's filter panel that hides it, to give the results more room. */
const FilterCollapseToggle = ({
  collapsed,
  onToggle,
  controls,
}: {
  collapsed: boolean;
  onToggle: () => void;
  /** The id of the panel it hides, when the page keeps that panel mounted (aria-controls). */
  controls?: string;
}) => (
  <div className="variant-filter-collapse-bar">
    <button
      type="button"
      className="variant-filter-collapse-toggle"
      aria-expanded={!collapsed}
      aria-controls={controls}
      onClick={onToggle}
    >
      <span className="variant-filter-dropdown-caret" aria-hidden="true">
        ▾
      </span>
      <span>{collapsed ? 'Show filters' : 'Hide filters'}</span>
    </button>
  </div>
);

export default FilterCollapseToggle;
