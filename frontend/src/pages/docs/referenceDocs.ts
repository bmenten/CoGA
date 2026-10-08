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
  markdown: string;
}

export const referenceDocs: ReferenceDoc[] = [
  { slug: 'data-import', markdown: dataImport },
  { slug: 'sample-qc', markdown: sampleQc },
  { slug: 'monarch-integration', markdown: monarchIntegration },
  { slug: 'acmg-classification', markdown: acmgClassification },
  { slug: 'haplotype-segregation', markdown: haplotypeSegregation },
  { slug: 'monogenic-nipt', markdown: monogenicNipt },
  { slug: 'sv-second-hit', markdown: svSecondHit },
  { slug: 'clinical-traceability', markdown: clinicalTraceability },
];

export const referenceDocBySlug = new Map(referenceDocs.map((doc) => [doc.slug, doc]));
