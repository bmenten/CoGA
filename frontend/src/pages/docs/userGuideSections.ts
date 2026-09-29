// The user guide's sections (#528): what the page lists — id, title, summary and the
// workspace quick links — while each section's text lives in Markdown under
// src/content/docs/user-guide/<id>.md, where it can be read and edited as prose.
// The guide is part of the information for safety (TF-15): the Markdown was converted
// from the JSX guide, and UserGuideContent.test.tsx holds it to what that guide said.

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
    id: 'orientation',
    title: 'How CoGA is organised',
    summary:
      'Understand the scoped data model — projects, families, samples, assemblies, and review state — before you start interpreting.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Start here' },
      { label: 'Families', to: '/families', note: 'Case catalog' },
    ],
  },
  {
    id: 'quick-start',
    title: 'Quick start',
    summary: 'Two common entry points: open an existing case, or set up a new one.',
    quickLinks: [
      { label: 'Dashboard', to: '/dashboard', note: 'Search' },
      { label: 'Family Builder', to: '/family-builder', note: 'New case' },
      { label: 'Package Import', to: '/package-import', note: 'Bulk import' },
      { label: 'Gene explorer', to: '/genes', note: 'Locus-first' },
      { label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' },
    ],
  },
  {
    id: 'case-setup',
    title: 'Case setup and data import',
    summary:
      'Create families and samples, import data packages, and understand what each assay layer unlocks.',
    quickLinks: [
      { label: 'Family Builder', to: '/family-builder', note: 'Manual pedigree' },
      { label: 'Package Import', to: '/package-import', note: 'Folder packages' },
      { label: 'Upload sample data', to: '/upload-data', note: 'Assays' },
      { label: 'Reference data', to: '/reference-data', note: 'Assembly layers' },
    ],
  },
  {
    id: 'phenotypes-and-panels',
    title: 'Phenotypes and gene panels',
    summary:
      'Anchor interpretation in the patient phenotype with HPO, and constrain searches with reusable gene panels.',
    quickLinks: [
      { label: 'HPO browser', to: '/hpo', note: 'Phenotype terms' },
      { label: 'Panel catalog', to: '/panels', note: 'Gene sets' },
    ],
  },
  {
    id: 'phenotype-matching',
    title: 'Phenotype matching (Monarch Initiative)',
    summary:
      'Connect the patient’s HPO phenotypes to genes and diseases through the Monarch knowledge graph — on the gene profile and as a ranked “candidate genes” panel in the family.',
    quickLinks: [
      { label: 'Gene explorer', to: '/genes', note: 'Gene–disease & phenotypes' },
      { label: 'Families', to: '/families', note: 'Candidate-gene panel' },
    ],
  },
  {
    id: 'family-workspace',
    title: 'The family workspace',
    summary:
      'The case dashboard: pedigree, review summaries, an editable region of interest, and one entry point per analysis.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a family' }],
  },
  {
    id: 'sample-qc',
    title: 'Sample-integrity QC',
    summary:
      'An automated check — before you interpret — that a family’s samples are who the pedigree says: catches swaps, mislabelled relationships, wrong-sex labels, contamination and consanguinity.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Sample QC button' }],
  },
  {
    id: 'small-variant-filtering',
    title: 'Small-variant prioritisation',
    summary:
      'The filter workbench: location, inheritance, consequence, ClinVar, frequency, in-silico scores, transcripts, and tags — with reusable presets.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Per-family search' }],
  },
  {
    id: 'variant-prioritisation',
    title: 'Phenotype-driven variant prioritisation (Exomiser-style)',
    summary:
      'One click ranks a family’s rare, impactful, segregating variants by how well each gene matches the patient’s phenotypes — with a transparent, explainable score.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Apply the preset' }],
  },
  {
    id: 'interpretation-and-review',
    title: 'Interpretation and review state',
    summary:
      'Record an ACMG classification, tags, and notes per variant, and keep that review state consistent across the team.',
  },
  {
    id: 'acmg-classification',
    title: 'Semi-automatic ACMG classification',
    summary:
      'A guided ACMG/AMP classifier that pre-evaluates criteria from the variant, trio and gene data, scores them on a points scale, and stays fully overridable.',
  },
  {
    id: 'clinical-report',
    title: 'Clinical report, traceability & sign-out',
    summary:
      'Draft a report from the reported variants, and lock the result to exactly what produced it: a version footer, evidence-drift warnings, an immutable audit trail, and a frozen, content-hashed case sign-out.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Report link → Variants' }],
  },
  {
    id: 'specialised-analyses',
    title: 'Structural variants, repeats, Paraphase, and mtDNA',
    summary:
      'Specialised review surfaces for events that small-variant tables do not capture.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a family' }],
  },
  {
    id: 'visualization',
    title: 'Genome visualisation and follow-up',
    summary:
      'Move from a candidate row into whole-genome, per-chromosome, Circos, and IGV views.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Viewers live per family' }],
  },
  {
    id: 'haplotype-segregation',
    title: 'Haplotype segregation analysis',
    summary:
      'A PGT tool: trace the four grandparental haplotypes through the family to read off which embryos inherited the disease haplotype(s) — with the raw markers to catch recombinations and artifacts.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Opens in the chromosome view' }],
  },
  {
    id: 'monogenic-nipt',
    title: 'Monogenic NIPT (cell-free DNA)',
    summary:
      'Screen a pregnancy for single-gene disorders from maternal-plasma cfDNA against a paternal sample — the fetal genotype is inferred from the allele fraction, never sequenced directly.',
    quickLinks: [{ label: 'Families', to: '/families', note: 'Open a NIPT family' }],
  },
  {
    id: 'gene-explorer',
    title: 'Gene Explorer',
    summary:
      'A locus-first gene profile: transcript overview with clinical badges, constraint metrics, and disease associations.',
    quickLinks: [{ label: 'Gene explorer', to: '/genes', note: 'Search a gene' }],
  },
  {
    id: 'variant-explorer',
    title: 'Global Small Variant Explorer',
    summary:
      'A variant-centric, cross-project view: how often a variant occurs, in which families, and with what review state.',
    quickLinks: [{ label: 'Variant explorer', to: '/variant-explorer', note: 'Cross-cohort' }],
  },
  {
    id: 'administration',
    title: 'Administration',
    summary:
      'The admin dashboard, grouped by operational domain: reference data, users & access, data management, variant configuration, database operations, and audit logs.',
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
    summary: 'Quick definitions for the terms used throughout CoGA.',
  },
];

export const guideSections: GuideSection[] = sections.map((section) => ({
  ...section,
  markdown: markdownFor(section.id),
}));
