// The page states stylediff.mjs renders. The families are the ones the seed scripts load: the
// golden trio (small variants, SVs, a report), the demo quartet (SVs, BED tracks, repeats) and
// the monogenic NIPT demo family (see README.md).
const FAMILIES = ['FAM_TRIO', 'demo_family', 'FAM_NIPT_DEMO'];
const FAMILY_PAGES = [
  '', '/small-variants', '/structural-variants', '/variant-summary', '/mitochondrial-dna',
  '/repeat-expansions', '/paraphase', '/qc', '/report', '/report?view=live', '/roi-markers',
  '/nipt', '/nipt/report', '/genome', '/chromosome/1', '/circos',
];
const DOCS = [
  'acmg-classification', 'clinical-traceability', 'data-import', 'haplotype-segregation',
  'monarch-integration', 'monogenic-nipt', 'sample-qc', 'sv-second-hit',
];
const APP = [
  '/', '/dashboard', '/projects', '/family-builder', '/package-import', '/upload-data', '/genes',
  '/hpo', '/panels', '/reference-data', '/settings', '/new-features', '/docs', '/variant-explorer',
  '/cnv-explorer', '/admin', '/admin/access/projects', '/admin/access/users', '/admin/data',
  '/admin/data/clickhouse', '/admin/data/families', '/admin/data/families/FAM_TRIO/structure',
  '/admin/data/family-statuses', '/admin/data/logs', '/admin/data/presets',
  '/admin/data/qc-thresholds', '/admin/data/tags', '/admin/data/upload', '/admin/gene-reference',
  '/admin/hpo', '/admin/monitoring/audit-logs', '/admin/operations/clickhouse',
  '/admin/reference/assemblies', '/admin/reference/gene-panels', '/admin/reference/gene-reference',
  '/admin/reference/hpo', '/admin/reference/monarch', '/admin/variants/presets',
  '/admin/variants/tags', '/no-such-page',
];

/** Rendered before signing in. */
export const PUBLIC = ['/login', '/signup'];
export const PAGES = [
  ...APP,
  ...DOCS.map((doc) => `/docs/reference/${doc}`),
  ...FAMILIES.flatMap((family) => FAMILY_PAGES.map((page) => `/families/${family}${page}`)),
];
/** The clinical reports, also compared in print media. */
export const PRINT = [
  '/families/FAM_TRIO/report',
  '/families/FAM_TRIO/report?view=live',
  '/families/FAM_NIPT_DEMO/nipt/report',
];
