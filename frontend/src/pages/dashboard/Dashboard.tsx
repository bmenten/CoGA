import React, { useState } from 'react';
import { Link } from 'react-router';
import { isAdmin } from '../../lib/auth';
import ProjectCatalogWorkspace from '../../components/ProjectCatalogWorkspace';

const Dashboard: React.FC = () => {
  const userIsAdmin = isAdmin();
  const [catalogSearch, setCatalogSearch] = useState('');

  return (
    <div className="page-shell space-y-8 dashboard-page">
      <section className="surface-card page-top-card dashboard-top-card">
        <div className="dashboard-catalog-header">
          <div className="dashboard-catalog-intro">
            <p className="page-kicker">Workspace</p>
            <h1 className="catalog-card-title">Project catalog</h1>
            <p className="catalog-card-copy">
              Review projects, linked families, and available samples from one dashboard workspace.
            </p>
          </div>
          <label className="field-label family-catalog-search dashboard-catalog-search">
            Search projects, families, or samples
            <input
              type="text"
              placeholder="Search projects, families, or samples..."
              value={catalogSearch}
              onChange={(event) => setCatalogSearch(event.target.value)}
            />
          </label>
        </div>
        <div className="compact-toolbar dashboard-toolbar dashboard-top-card-actions">
          <Link to="/genes" className="button-secondary hover:no-underline">
            Gene explorer
          </Link>
          <Link to="/variant-explorer" className="button-secondary hover:no-underline">
            Variant explorer
          </Link>
          <Link to="/cnv-explorer" className="button-secondary hover:no-underline">
            Clinical CNV explorer
          </Link>
          <Link to="/panels" className="button-secondary hover:no-underline">
            Panel catalog
          </Link>
          <Link to="/family-builder" className="button-secondary hover:no-underline">
            Family Builder
          </Link>
          {userIsAdmin && (
            <Link to="/admin" className="button-secondary hover:no-underline">
              Admin
            </Link>
          )}
          <Link to="/docs" className="button-grey hover:no-underline">
            User guide
          </Link>
        </div>
        <div className="dashboard-top-card-catalog">
          <ProjectCatalogWorkspace
            embedded
            searchTerm={catalogSearch}
            onSearchTermChange={setCatalogSearch}
          />
        </div>
      </section>

      {userIsAdmin && (
        <section className="surface-card-flat catalog-card dashboard-admin-card">
          <div className="catalog-card-header">
            <div>
              <p className="page-kicker">Administration</p>
              <h2 className="catalog-card-title">Operational controls and setup</h2>
            </div>
            <span className="badge-chip badge-chip--signature">Admin</span>
          </div>
          <div className="compact-toolbar dashboard-toolbar">
            <Link to="/admin" className="button-secondary hover:no-underline">
              Open Admin dashboard
            </Link>
            <Link to="/admin/data/upload" className="button-secondary hover:no-underline">
              Package Import
            </Link>
            <Link to="/admin/data/families" className="button-secondary hover:no-underline">
              Family & sample data
            </Link>
            <Link to="/admin/monitoring/audit-logs" className="button-secondary hover:no-underline">
              Audit logs
            </Link>
          </div>
        </section>
      )}
    </div>
  );
};

export default Dashboard;
