import React from 'react';
import { formatCount, type FamilyInventoryPage, type FamilySummaryData } from './dataManagementTypes';

interface DataInventorySidebarProps {
  search: string;
  page: number;
  inventoryPage?: FamilyInventoryPage;
  inventoryFetching: boolean;
  selectedFamilyId: string | null;
  summaries: FamilySummaryData[];
  onSearchChange: (value: string) => void;
  onPageChange: (page: number) => void;
  onSelectFamily: (familyId: string) => void;
}

const DataInventorySidebar: React.FC<DataInventorySidebarProps> = ({
  search,
  page,
  inventoryPage,
  inventoryFetching,
  selectedFamilyId,
  summaries,
  onSearchChange,
  onPageChange,
  onSelectFamily,
}) => {
  const totalPages = Math.max(
    Math.ceil((inventoryPage?.total ?? 0) / (inventoryPage?.page_size || 25)),
    1,
  );

  return (
    <aside className="surface-card admin-data-sidebar">
      <div className="space-y-4">
        <div className="space-y-2">
          <p className="page-kicker">Families</p>
          <h2 className="section-title">Select a family</h2>
          <p className="catalog-card-copy">
            Search by family or sample identifier, then manage deletions from one focused detail
            view.
          </p>
        </div>

        <label className="field-label">
          Search families or samples
          <input
            type="text"
            value={search}
            onChange={(event) => onSearchChange(event.target.value)}
            placeholder="demo_family or son"
          />
        </label>
      </div>

      <div className="admin-family-list">
        {summaries.length === 0 ? (
          <p className="table-empty">No families match the current search.</p>
        ) : (
          summaries.map((family) => {
            const isActive = family.family_id === selectedFamilyId;
            const isDemo = Boolean(family.metadata.demo);
            return (
              <button
                key={family.family_id}
                type="button"
                className={`admin-family-card${isActive ? ' admin-family-card--active' : ''}`}
                onClick={() => onSelectFamily(family.family_id)}
              >
                <div className="admin-family-card-header">
                  <div>
                    <h3>{family.family_id}</h3>
                    <p>
                      {family.sample_count} {family.sample_count === 1 ? 'sample' : 'samples'}
                    </p>
                  </div>
                  {isDemo && <span className="badge-chip badge-chip--signature">Demo</span>}
                </div>
                <div className="admin-family-card-metrics">
                  <span className="badge-chip">
                    {formatCount(family.track_counts.small_variants)} SNVs
                  </span>
                  <span className="badge-chip">
                    {formatCount(family.track_counts.structural_variants)} SVs
                  </span>
                </div>
              </button>
            );
          })
        )}
      </div>
      <div className="inline-actions">
        <button
          type="button"
          className="button-secondary"
          disabled={page <= 1 || inventoryFetching}
          onClick={() => onPageChange(Math.max(page - 1, 1))}
        >
          Previous
        </button>
        <span className="analysis-count">
          Page {inventoryPage?.page ?? page} of {totalPages}
        </span>
        <button
          type="button"
          className="button-secondary"
          disabled={inventoryFetching || page >= totalPages}
          onClick={() => onPageChange(page + 1)}
        >
          Next
        </button>
      </div>
    </aside>
  );
};

export default DataInventorySidebar;
