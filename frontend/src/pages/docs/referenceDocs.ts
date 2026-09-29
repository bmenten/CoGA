// In-depth reference docs, authored as Markdown and bundled so they render in-app
// (the repository is private, so external links to the .md files are not usable).
// They hold the clinical rules for lab users; the user guide links to them.
// Add a new doc by dropping a .md under src/content/docs and registering it here.
import dataImport from '../../content/docs/data-import.md?raw';
import sampleQc from '../../content/docs/sample-qc.md?raw';
import monarchIntegration from '../../content/docs/monarch-integration.md?raw';
import acmgClassification from '../../content/docs/acmg-classification.md?raw';
import haplotypeSegregation from '../../content/docs/haplotype-segregation.md?raw';
import monogenicNipt from '../../content/docs/monogenic-nipt.md?raw';
import svSecondHit from '../../content/docs/sv-second-hit.md?raw';
import clinicalTraceability from '../../content/docs/clinical-traceability.md?raw';

export interface ReferenceDoc {
  slug: string;
  title: string;
  summary: string;
  markdown: string;
}

export const referenceDocs: ReferenceDoc[] = [
  {
    slug: 'data-import',
    title: 'Data import',
    summary:
      'Family Builder and Package Import, who may do what, reference data per assembly, what each kind of data unlocks, and the package steps and checks.',
    markdown: dataImport,
  },
  {
    slug: 'sample-qc',
    title: 'Sample-integrity QC',
    summary:
      'The checks per application — sex, relatedness and consanguinity, Mendelian errors, and the NIPT cfDNA checks — with their thresholds.',
    markdown: sampleQc,
  },
  {
    slug: 'monarch-integration',
    title: 'Phenotype matching with Monarch',
    summary:
      'What the gene ↔ disease ↔ HPO graph links, the gene-profile associations, the candidate-gene panel, and how the phenotype score feeds the ranking and PP4.',
    markdown: monarchIntegration,
  },
  {
    slug: 'acmg-classification',
    title: 'Semi-automatic ACMG classification',
    summary:
      'The points and class bands, the VUS tiers, every pre-evaluation rule, the criteria left to you, the mtDNA rule set, and the ClinGen CNV classifier.',
    markdown: acmgClassification,
  },
  {
    slug: 'haplotype-segregation',
    title: 'Haplotype segregation analysis',
    summary:
      'The PGT haplotype track: the colours and risk line, how the disease haplotype is found, the embryo calls and their warnings, and the ROI marker review.',
    markdown: haplotypeSegregation,
  },
  {
    slug: 'monogenic-nipt',
    title: 'Monogenic NIPT (cfDNA)',
    summary:
      'The two-sample trio, the required input, the fetal-fraction estimate, the eight categories and their flags, and the inheritance presets.',
    markdown: monogenicNipt,
  },
  {
    slug: 'sv-second-hit',
    title: 'SNV + SV compound heterozygosity',
    summary:
      'Genes hit by both a small variant and a structural variant: the SV badge, the "Also hit by an SV" filter, and how trans or cis is decided.',
    markdown: svSecondHit,
  },
  {
    slug: 'clinical-traceability',
    title: 'Report traceability & sign-out',
    summary:
      'The version footer, the evidence-drift banner, the audit trail, the three sign-out checks, and how to tell whether a page is the signed report.',
    markdown: clinicalTraceability,
  },
];

export const referenceDocBySlug = new Map(referenceDocs.map((doc) => [doc.slug, doc]));
