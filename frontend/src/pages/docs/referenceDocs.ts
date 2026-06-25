// In-depth reference docs, authored as Markdown and bundled so they render in-app
// (the repository is private, so external links to the .md files are not usable).
// Add a new doc by dropping a .md under src/content/docs and registering it here.
import dataImport from '../../content/docs/data-import.md?raw';
import sampleQc from '../../content/docs/sample-qc.md?raw';
import monarchIntegration from '../../content/docs/monarch-integration.md?raw';
import acmgClassification from '../../content/docs/acmg-classification.md?raw';
import haplotypeSegregation from '../../content/docs/haplotype-segregation.md?raw';
import monogenicNipt from '../../content/docs/monogenic-nipt.md?raw';
import variantRankingCache from '../../content/docs/variant-ranking-cache.md?raw';
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
      'Family Builder vs Package Import, assembly-scoped reference data, what each assay layer unlocks, the manifest / dry-run / import flow, and common pitfalls.',
    markdown: dataImport,
  },
  {
    slug: 'sample-qc',
    title: 'Sample-integrity QC',
    summary:
      'Application-aware sample QC: sex, relatedness / consanguinity, Mendelian-error rate, and the monogenic-NIPT cfDNA checks — with thresholds and data sources.',
    markdown: sampleQc,
  },
  {
    slug: 'monarch-integration',
    title: 'Phenotype matching with Monarch',
    summary:
      'The gene ↔ disease ↔ HPO knowledge graph, the gene-profile blocks, the information-content candidate-gene ranking, and how it feeds variant prioritisation and PP4.',
    markdown: monarchIntegration,
  },
  {
    slug: 'acmg-classification',
    title: 'Semi-automatic ACMG classification',
    summary:
      'The pre-check and exclusion logic, the points-to-class mapping, the VUS hot/warm/cold sub-tiers, the mtDNA rule set, and what is left for manual review.',
    markdown: acmgClassification,
  },
  {
    slug: 'haplotype-segregation',
    title: 'Haplotype segregation analysis',
    summary:
      'The PGT Haplotype track: how grandparental founder colours, the disease haplotype, and per-embryo affected/carrier calls are derived from phased genotypes and the pedigree.',
    markdown: haplotypeSegregation,
  },
  {
    slug: 'monogenic-nipt',
    title: 'Monogenic NIPT (cfDNA)',
    summary:
      'The cell-free-DNA two-sample trio workflow plus the fetal-fraction estimator and the eight-category per-variant classification algorithm.',
    markdown: monogenicNipt,
  },
  {
    slug: 'variant-ranking-cache',
    title: 'Prioritised ranking & caching',
    summary:
      'How the phenotype-prioritised view is cached and kept fresh: automatic invalidation, background warming, the cache indicator, and serving a sub-panel instantly from the broader Mendeliome ranking.',
    markdown: variantRankingCache,
  },
  {
    slug: 'sv-second-hit',
    title: 'SNV + SV compound heterozygosity',
    summary:
      'The cross-type "second hit": flagging genes hit by both a small variant and a structural variant, the "Also hit by an SV" filter, and the trans/cis verdict by segregation or read phasing (deletion-unmasking).',
    markdown: svSecondHit,
  },
  {
    slug: 'clinical-traceability',
    title: 'Report traceability & sign-out',
    summary:
      'How the clinical report locks a result to what produced it: the version footer, evidence-drift surfacing, the immutable audit trail, and the frozen, content-hashed case sign-out.',
    markdown: clinicalTraceability,
  },
];

export const referenceDocBySlug = new Map(referenceDocs.map((doc) => [doc.slug, doc]));
