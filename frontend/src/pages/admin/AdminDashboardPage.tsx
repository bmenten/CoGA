import React from 'react';
import { Link } from 'react-router';

type AdminModule = {
  label: string;
  to: string;
  description: string;
};

type AdminGroup = {
  title: string;
  description: string;
  modules: AdminModule[];
};

const adminGroups: AdminGroup[] = [
  {
    title: 'Reference data',
    description: 'System-wide reference datasets and annotations.',
    modules: [
      {
        label: 'Species & assemblies',
        to: '/admin/reference/assemblies',
        description:
          'Per-assembly reference datasets (cytobands, genes, clinical CNVs, SegDups), gene metadata sync, dataset status, and updates.',
      },
      {
        label: 'Gene panels',
        to: '/admin/reference/gene-panels',
        description: 'Panel catalog, source metadata, and panel membership.',
      },
      {
        label: 'HPO terminology',
        to: '/admin/reference/hpo',
        description: 'Ontology terms, synonyms, relationships, release status, and sync.',
      },
      {
        label: 'Monarch data',
        to: '/admin/reference/monarch',
        description:
          'Gene–disease and disease–phenotype associations from the Monarch knowledge graph: view the loaded release and update to the latest.',
      },
      {
        label: 'Repeat catalogue',
        to: '/admin/reference/repeat-loci',
        description:
          "Record, per repeat locus, whether QA validated that TRGT's count is the clinical repeat number; until then counts near a boundary are marked.",
      },
    ],
  },
  {
    title: 'User & access management',
    description: 'Users, permissions, projects, and access assignments.',
    modules: [
      {
        label: 'Users',
        to: '/admin/access/users',
        description: 'User accounts, roles, and activation status.',
      },
      {
        label: 'Projects & access',
        to: '/admin/access/projects',
        description: 'Project catalog and family/sample access assignments.',
      },
    ],
  },
  {
    title: 'Data management',
    description: 'Imported and stored family, sample, and assay data.',
    modules: [
      {
        label: 'Family & sample data',
        to: '/admin/data/families',
        description: 'Inventory, project assignment, and deletion workflows.',
      },
      {
        label: 'Family statuses',
        to: '/admin/data/family-statuses',
        description: 'Curate the workflow statuses analysts assign to families.',
      },
      {
        label: 'Sequencing QC thresholds',
        to: '/admin/data/qc-thresholds',
        description: 'Set the warning and error cut-offs that flag an inadequate sequencing run.',
      },
      {
        label: 'Package import',
        to: '/admin/data/upload',
        description: 'Validate manifests and run package-based initial or incremental imports.',
      },
    ],
  },
  {
    title: 'Variant configuration',
    description: 'Interpretation labels and reusable variant filters.',
    modules: [
      {
        label: 'Variant tags',
        to: '/admin/variants/tags',
        description: 'Review tag definitions and project-scoped tag configuration.',
      },
      {
        label: 'Preset filters',
        to: '/admin/variants/presets',
        description: 'Saved small-variant filter presets for consistent review.',
      },
    ],
  },
  {
    title: 'Database & operations',
    description: 'Technical storage and operational controls.',
    modules: [
      {
        label: 'ClickHouse tables & operations',
        to: '/admin/operations/clickhouse',
        description:
          'Per-assembly table status plus manual maintenance: ensure, optimize, and gene-index rebuilds.',
      },
    ],
  },
  {
    title: 'Monitoring & audit',
    description: 'Traceability for operational and administrative activity.',
    modules: [
      {
        label: 'Audit logs',
        to: '/admin/monitoring/audit-logs',
        description: 'Request, user, status, and update-event history.',
      },
    ],
  },
];

const AdminDashboardPage: React.FC = () => (
  <div className="page-shell admin-compact space-y-5">
    <section className="surface-card page-top-card">
      <div className="page-header">
        <div className="space-y-1">
          <p className="page-kicker">Administration</p>
          <h1 className="catalog-card-title">Admin dashboard</h1>
          <p className="catalog-card-copy">
            Administrative workspaces grouped by operational domain.
          </p>
        </div>
      </div>
    </section>

    <div className="grid gap-4 xl:grid-cols-2">
      {adminGroups.map((group) => (
        <section key={group.title} className="surface-card-flat catalog-card">
          <div className="space-y-2">
            <h2 className="section-title">{group.title}</h2>
            <p className="section-copy">{group.description}</p>
          </div>
          <div className="hpo-term-grid">
            {group.modules.map((module) => (
              <Link key={module.to} to={module.to} className="hpo-term-card">
                <span className="font-semibold text-(--color-heading)">
                  {module.label}
                </span>
                <span className="table-subtle">{module.description}</span>
              </Link>
            ))}
          </div>
        </section>
      ))}
    </div>
  </div>
);

export default AdminDashboardPage;
