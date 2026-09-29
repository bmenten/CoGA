// The user guide's sections (#528): what the page lists — id, title, summary and the
// workspace quick links — while each section's text lives in Markdown under
// src/content/docs/user-guide/<id>.md, where it can be read and edited as prose.
// The guide is part of the information for safety (TF-15). Each section says what a page
// is for and how to use it, and links to the reference docs (referenceDocs.ts) for the
// rules. UserGuideContent.test.tsx holds the rendered text to its reviewed snapshot.

export type GuideLink = {
  label: string;
  to: string;
  note?: string;
};

export type GuideSection = {
  id: string;
  title: string;
  summary: string;
  quickLinks?: GuideLink[];
  markdown: string;
};

const sectionMarkdown = import.meta.glob<string>('../../content/docs/user-guide/*.md', {
  query: '?raw',
  import: 'default',
  eager: true,
});

const markdownFor = (id: string): string => {
  const markdown = sectionMarkdown[`../../content/docs/user-guide/${id}.md`];
  if (markdown === undefined) throw new Error(`user guide section ${id} has no Markdown`);
  return markdown;
};

const sections: Omit<GuideSection, 'markdown'>[] = [
  {
    id: 'quick-start',
    title: 'Quick start',
    summary:
      'Get an account, see how CoGA is organised, review a first case, and keep the five rules for safe use in mind.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Start here' },
      { label: 'Family Builder', to: '/family-builder', note: 'New case' },
      { label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' },
    ],
  },
  {
    id: 'case-setup',
    title: 'Case setup and data import',
    summary:
      'Create a family, import a data package, and know what each kind of data unlocks.',
    quickLinks: [
      { label: 'Family Builder', to: '/family-builder', note: 'New family' },
      { label: 'Package Import', to: '/package-import', note: 'Admin' },
      { label: 'Upload data', to: '/upload-data', note: 'Admin' },
      { label: 'Reference data', to: '/reference-data', note: 'Assemblies' },
    ],
  },
  {
    id: 'phenotype-matching',
    title: 'Phenotypes, panels and phenotype matching',
    summary:
      'Record HPO terms, focus a search with a gene panel, and match the phenotype to genes and diseases with Monarch.',
    quickLinks: [
      { label: 'Panel catalog', to: '/panels', note: 'Gene sets' },
      { label: 'Gene explorer', to: '/genes', note: 'Gene–disease links' },
    ],
  },
  {
    id: 'family-workspace',
    title: 'The family page',
    summary:
      'The case hub: status and assignment, one button per analysis and viewer, the region of interest, and the family members.',
    quickLinks: [{ label: 'Dashboard', to: '/dashboard', note: 'Find a family' }],
  },
  {
    id: 'sample-qc',
    title: 'Sample-integrity QC',
    summary:
      'Check, before you interpret, that the samples are who the pedigree says: swaps, wrong relationships, wrong sex, contamination and consanguinity.',
  },
  {
    id: 'small-variant-filtering',
    title: 'Small-variant prioritisation',
    summary:
      'The small-variant page: the default view, the filters and presets, the phenotype-driven ranking, and genes also hit by a structural variant.',
  },
  {
    id: 'interpretation-and-review',
    title: 'Interpretation and review',
    summary:
      'Tag, annotate and classify variants — with the semi-automatic ACMG classifier — and keep the review state per family.',
  },
  {
    id: 'clinical-report',
    title: 'Clinical report and sign-out',
    summary:
      'Draft the report from the tagged variants, see what changed, and sign it out into a version that can never change.',
  },
  {
    id: 'specialised-analyses',
    title: 'Structural variants, repeats, Paraphase and mtDNA',
    summary:
      'The analyses beyond small variants, including the CNV classifier and the variant summary.',
  },
  {
    id: 'visualization',
    title: 'Genome visualisation',
    summary:
      'From a candidate to its context: the genome and chromosome views, Circos and IGV, and how the small-variant track is drawn.',
  },
  {
    id: 'haplotype-segregation',
    title: 'Haplotype segregation (PGT)',
    summary:
      'Follow the parental haplotypes through the family to see which embryos inherited the disease haplotype — and when not to trust the call.',
  },
  {
    id: 'monogenic-nipt',
    title: 'Monogenic NIPT',
    summary:
      'Screen a pregnancy for single-gene disorders from maternal-plasma cfDNA and a paternal sample: set it up, read the page, print the report.',
  },
  {
    id: 'explorers',
    title: 'Gene, variant and CNV explorers',
    summary:
      'Questions beyond one case: a gene profile, a variant across the cohort, and the catalogue of clinical CNVs.',
    quickLinks: [
      { label: 'Gene explorer', to: '/genes', note: 'Search a gene' },
      { label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' },
      { label: 'Clinical CNV explorer', to: '/cnv-explorer', note: 'CNV catalogue' },
    ],
  },
  {
    id: 'administration',
    title: 'Administration',
    summary:
      'The admin dashboard: reference data, users and access, data management, variant configuration, database operations and audit logs.',
    quickLinks: [
      { label: 'Admin dashboard', to: '/admin', note: 'All tools' },
      { label: 'Family & sample data', to: '/admin/data/families', note: 'Inventory & import' },
      { label: 'Projects & access', to: '/admin/access/projects', note: 'Scoping' },
      { label: 'Users', to: '/admin/access/users', note: 'Accounts' },
      { label: 'Audit logs', to: '/admin/monitoring/audit-logs', note: 'Activity' },
    ],
  },
  {
    id: 'glossary',
    title: 'Glossary',
    summary: 'Short definitions of the terms used in CoGA.',
  },
];

export const guideSections: GuideSection[] = sections.map((section) => ({
  ...section,
  markdown: markdownFor(section.id),
}));
