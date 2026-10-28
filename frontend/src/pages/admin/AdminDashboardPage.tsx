import React from 'react';
import { Link } from 'react-router';

import InfoTip from '../../components/InfoTip';
import PageHeader from '../../components/PageHeader';

type AdminModule = {
  label: string;
  to: string;
  description: string;
};

type AdminGroupIcon = 'reference' | 'access' | 'data' | 'variants' | 'database' | 'audit';

type AdminGroup = {
  title: string;
  description: string;
  icon: AdminGroupIcon;
  modules: AdminModule[];
};

const adminGroups: AdminGroup[] = [
  {
    title: 'Reference data',
    description: 'System-wide reference datasets and annotations.',
    icon: 'reference',
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
    icon: 'access',
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
    icon: 'data',
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
    icon: 'variants',
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
    icon: 'database',
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
    icon: 'audit',
    modules: [
      {
        label: 'Audit logs',
        to: '/admin/monitoring/audit-logs',
        description: 'Request, user, status, and update-event history.',
      },
    ],
  },
];

// One small line icon per group, drawn in the current colour. Decorative: the heading names it.
const ICON_PATHS: Record<AdminGroupIcon, string> = {
  // A book.
  reference: 'M3 2.5h7.5a1.5 1.5 0 0 1 1.5 1.5v9.5H4.5A1.5 1.5 0 0 1 3 12V2.5Zm0 9.5a1.5 1.5 0 0 1 1.5-1.5H12',
  // Two people.
  access: 'M6 7a2.25 2.25 0 1 0 0-4.5A2.25 2.25 0 0 0 6 7Zm-4 6.5c0-2.2 1.8-4 4-4s4 1.8 4 4M11 3a2 2 0 0 1 0 4m1.5 2.75c1 .6 1.5 1.9 1.5 3.75',
  // A folder.
  data: 'M2 4.5A1.5 1.5 0 0 1 3.5 3h3l1.5 1.5h4.5A1.5 1.5 0 0 1 14 6v5.5a1.5 1.5 0 0 1-1.5 1.5h-9A1.5 1.5 0 0 1 2 11.5v-7Z',
  // Three sliders.
  variants: 'M3 4h10M3 8h10M3 12h10M6 2.75v2.5M10 6.75v2.5M5 10.75v2.5',
  // A database cylinder.
  database: 'M3 4c0-1.1 2.2-2 5-2s5 .9 5 2-2.2 2-5 2-5-.9-5-2Zm0 0v8c0 1.1 2.2 2 5 2s5-.9 5-2V4M3 8c0 1.1 2.2 2 5 2s5-.9 5-2',
  // A pulse line.
  audit: 'M1.5 8.5h3l1.5-4 2.5 7 1.5-3h4.5',
};

const GroupIcon: React.FC<{ icon: AdminGroupIcon }> = ({ icon }) => (
  <svg className="admin-nav-icon" viewBox="0 0 16 16" aria-hidden="true" focusable="false">
    <path d={ICON_PATHS[icon]} />
  </svg>
);

const AdminDashboardPage: React.FC = () => (
  <div className="page-shell admin-compact space-y-5">
    <PageHeader
      kicker="Administration"
      title="Admin dashboard"
      description="Administrative workspaces grouped by operational domain."
    />

    <nav className="surface-card admin-nav" aria-label="Admin workspaces">
      {adminGroups.map((group) => (
        <section key={group.title} className="admin-nav-group" aria-labelledby={`admin-group-${group.icon}`}>
          <h2 id={`admin-group-${group.icon}`} className="admin-nav-title">
            <GroupIcon icon={group.icon} />
            {group.title}
          </h2>
          <p className="admin-nav-copy">{group.description}</p>
          <ul className="admin-nav-list">
            {group.modules.map((module) => (
              <li key={module.to}>
                {/* The description is the link's tooltip and, while shown, its accessible
                    description; the link's name stays its label. */}
                <InfoTip label={module.description} interactiveChild>
                  <Link to={module.to} className="admin-nav-link">
                    {module.label}
                  </Link>
                </InfoTip>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </nav>
  </div>
);

export default AdminDashboardPage;
