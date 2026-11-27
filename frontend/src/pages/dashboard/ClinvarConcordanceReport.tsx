import React from 'react';

import type { ClinvarConcordanceOut, FamilyImportDatasetSummary } from '../../lib/apiSchema.generated';

// What a compared callset's concordance says in one sentence.
const comparedText = (concordance: ClinvarConcordanceOut) =>
  `Compared with CoGA's ClinVar ${concordance.annotated_release}, the annotation's release: ` +
  `${concordance.agree.toLocaleString()} allele(s) agree, ${concordance.differ.toLocaleString()} differ.`;

/** Each small-variant dataset's ClinVar concordance with CoGA's release of the same ClinVar
 * release: the alleles that agree, and each difference with its reason; or why it was not
 * compared. */
const ClinvarConcordanceReport: React.FC<{ datasets: FamilyImportDatasetSummary[] }> = ({ datasets }) => {
  const reported = datasets.filter(
    (dataset): dataset is FamilyImportDatasetSummary & { clinvar_concordance: ClinvarConcordanceOut } =>
      dataset.clinvar_concordance != null,
  );
  if (reported.length === 0) return null;
  return (
    <div className="space-y-3">
      <h3 className="eyebrow-label">ClinVar concordance</h3>
      {reported.map(({ dataset_type: dataset, clinvar_concordance: concordance }) => (
        <section key={dataset} aria-label={`ClinVar concordance of ${dataset}`} className="space-y-2">
          <p className={concordance.differ > 0 ? 'status-note status-note--warning' : 'status-note status-note--info'}>
            {dataset}: {concordance.compared ? comparedText(concordance) : concordance.reason}
          </p>
          {concordance.differences.length > 0 ? (
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table">
                <thead>
                  <tr>
                    <th>Variant</th>
                    <th>Allele</th>
                    <th>Reason</th>
                    <th>Annotation</th>
                    <th>CoGA</th>
                  </tr>
                </thead>
                <tbody>
                  {concordance.differences.map((difference) => (
                    <tr key={`${difference.variant_id ?? ''}-${difference.allele}`}>
                      <td>{difference.variant_id ?? '—'}</td>
                      <td>{difference.allele}</td>
                      <td>{difference.reason}</td>
                      <td>{difference.annotated ?? '—'}</td>
                      <td>{difference.coga ?? '—'}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
              {concordance.differ > concordance.differences.length ? (
                <p className="text-(--color-text-muted)">
                  The first {concordance.differences.length} of {concordance.differ.toLocaleString()} differences.
                </p>
              ) : null}
            </div>
          ) : null}
        </section>
      ))}
    </div>
  );
};

export default ClinvarConcordanceReport;
