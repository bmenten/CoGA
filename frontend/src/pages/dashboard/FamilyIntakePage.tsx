import React from 'react';
import { Link } from 'react-router-dom';

import FamilyIntakePanel from './FamilyIntakePanel';
import FamilyPackageImportPanel from './FamilyPackageImportPanel';
import { isAdmin } from '../../lib/auth';

const FamilyIntakePage: React.FC = () => {
  return (
    <div className="page-shell space-y-5 family-intake-page">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Intake</p>
            <h1 className="catalog-card-title">Family intake</h1>
            <p className="catalog-card-copy">
              Create family and sample metadata manually. Admins can validate manifests and import
              family data packages from this workspace.
            </p>
          </div>
          <Link to="/dashboard" className="subtle-link">
            Back to dashboard
          </Link>
        </div>
      </section>

      <FamilyIntakePanel />
      {isAdmin() ? <FamilyPackageImportPanel /> : null}
    </div>
  );
};

export default FamilyIntakePage;
