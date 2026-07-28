import React from 'react';
import { Link } from 'react-router';

import FamilyIntakePanel from './FamilyIntakePanel';

const FamilyBuilderPage: React.FC = () => {
  return (
    <div className="page-shell space-y-5 family-intake-page">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Intake</p>
            <h1 className="catalog-card-title">Family Builder</h1>
            <p className="catalog-card-copy">
              Create family and sample metadata by building a pedigree manually or from a PED file.
              To bulk-import a prepared data package instead, use Package Import.
            </p>
          </div>
          <Link to="/dashboard" className="subtle-link">
            Back to dashboard
          </Link>
        </div>
      </section>

      <FamilyIntakePanel />
    </div>
  );
};

export default FamilyBuilderPage;
