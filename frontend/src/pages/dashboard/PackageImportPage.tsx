import React from 'react';
import { Link } from 'react-router';

import FamilyPackageImportPanel from './FamilyPackageImportPanel';

const PackageImportPage: React.FC = () => {
  return (
    <div className="page-shell space-y-5 family-intake-page">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Intake</p>
            <h1 className="catalog-card-title">Package Import</h1>
            <p className="catalog-card-copy">
              Validate manifests and bulk-import family data packages from a folder. To create a
              family by hand, use Family Builder.
            </p>
          </div>
          <Link to="/dashboard" className="subtle-link">
            Back to dashboard
          </Link>
        </div>
      </section>

      <FamilyPackageImportPanel />
    </div>
  );
};

export default PackageImportPage;
