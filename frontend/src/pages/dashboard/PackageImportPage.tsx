import React from 'react';
import { Link } from 'react-router';

import ImportDeliveriesPanel from './ImportDeliveriesPanel';
import ImportJobsPanel from './ImportJobsPanel';

const PackageImportPage: React.FC = () => {
  return (
    <div className="page-shell space-y-5 family-intake-page">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Intake</p>
            <h1 className="catalog-card-title">Package import</h1>
            <p className="catalog-card-copy">
              Check, correct and approve the family packages the pipelines deliver, or add a
              folder by hand, and follow their imports. To create a family by hand, use Family
              Builder.
            </p>
          </div>
          <Link to="/dashboard" className="subtle-link">
            Back to dashboard
          </Link>
        </div>
      </section>

      <ImportDeliveriesPanel />

      <ImportJobsPanel />
    </div>
  );
};

export default PackageImportPage;
