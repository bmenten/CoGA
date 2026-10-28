import React from 'react';
import type { ApiParaphaseGeneResult, ApiRepeatExpansionRow, ApiRepeatStatus } from '../../lib/apiTypes';
import { repeatStatusLabel } from '../../lib/repeatStatus';
import { COLLABORATION_QUICK_TAGS } from './smallVariantSearch';
import { paraphaseStatusWords, type SignedFindingReview } from './signedReportRecord';
import { FINDING_DRIFT_LABEL, differsFromStatus, findingClassificationLabel, type FindingKind } from './findingReview';

/**
 * The repeat loci and Paraphase regions marked Report (docs/repeat-paraphase-review-design.md,
 * B2, B7): on the live report from the pages' tables, on a signed version from its record. Each
 * names its gene and disease, every member's call with CoGA's status, the analyst's
 * classification and note, whether that classification agrees with none of the calls (H18),
 * and whether the calls changed since the review (B5). The signed version lists every call
 * besides, under what the analysis looked at.
 */
export interface ReportedFinding {
  kind: FindingKind;
  key: string;
  gene: string;
  disease: string | null;
  classification: string | null;
  differs: boolean;
  calls: { sample: string; call: string | null; status: string }[];
  note: string | null;
  drift: { label: string; changes: string[] } | null;
}

const statusWords = (kind: FindingKind, status: string | null): string =>
  kind === 'repeat' ? repeatStatusLabel((status ?? 'unknown') as ApiRepeatStatus) : paraphaseStatusWords(status);

const driftOf = (drift: { status: string; changes: string[] } | null | undefined) =>
  drift && drift.status !== 'unchanged'
    ? { label: FINDING_DRIFT_LABEL[drift.status] ?? 'Changed since review', changes: drift.changes }
    : null;

const isReported = (tags: string[] | undefined) => Boolean(tags?.includes(COLLABORATION_QUICK_TAGS.report));

/** The live report's entry of a repeat locus marked Report; null when it is not. */
export const reportedRepeatFinding = (row: ApiRepeatExpansionRow, sampleOrder: string[]): ReportedFinding | null => {
  if (!isReported(row.review?.tags)) return null;
  const calls = sampleOrder
    .map((sample) => row.calls[sample])
    .filter(Boolean)
    .map((call) => {
      const counts = call.alleles.map((allele) => allele.repeat_count).filter((count) => count != null);
      return {
        sample: call.sample,
        call: counts.length ? counts.join(' / ') : null,
        status: repeatStatusLabel(call.status, call.review_reason),
      };
    });
  return {
    kind: 'repeat',
    key: row.locus_id,
    gene: row.display_name || row.gene,
    disease: row.disease && row.disease !== row.locus_id ? row.disease : null,
    classification: findingClassificationLabel('repeat', row.review?.classification),
    differs: differsFromStatus(
      'repeat',
      row.review?.classification,
      Object.values(row.calls).map((call) => call.status),
    ),
    calls,
    note: row.review?.note ?? null,
    drift: driftOf(row.review?.drift),
  };
};

/** The live report's entry of a Paraphase region marked Report; null when it is not. */
export const reportedParaphaseFinding = (
  gene: ApiParaphaseGeneResult,
  sampleOrder: string[],
): ReportedFinding | null => {
  if (!isReported(gene.review?.tags)) return null;
  const calls = sampleOrder
    .map((sample) => gene.samples[sample])
    .filter(Boolean)
    .map((result) => {
      const metric = (result.copy_number_metrics ?? [])[0];
      return {
        sample: result.sample,
        call: metric ? `${metric.label} ${metric.value ?? 'no call'}` : null,
        status: statusWords('paraphase', result.clinical_status ?? null),
      };
    });
  return {
    kind: 'paraphase',
    key: gene.gene_symbol,
    gene: gene.region_info?.display_name || gene.gene_symbol,
    disease: (gene.region_info?.disorders ?? []).map((disorder) => disorder.name).join('; ') || null,
    classification: findingClassificationLabel('paraphase', gene.review?.classification),
    differs: differsFromStatus(
      'paraphase',
      gene.review?.classification,
      Object.values(gene.samples).map((result) => result.clinical_status),
    ),
    calls,
    note: gene.review?.note ?? null,
    drift: driftOf(gene.review?.drift),
  };
};

/** A signed version's entry of a review the record marks reported; null when it is not. */
export const signedReportedFinding = (review: SignedFindingReview): ReportedFinding | null =>
  review.reported
    ? {
        kind: review.kind,
        key: review.key,
        gene: review.gene ?? review.key,
        disease: review.disease,
        classification: review.classificationLabel ?? findingClassificationLabel(review.kind, review.classification),
        differs: differsFromStatus(
          review.kind,
          review.classification,
          review.calls.map((call) => call.status),
        ),
        calls: review.calls.map((call) => ({ ...call, status: statusWords(review.kind, call.status) })),
        note: review.note,
        drift: driftOf(review.drift),
      }
    : null;

const NOUN: Record<FindingKind, string> = { repeat: 'repeat locus', paraphase: 'Paraphase region' };

export const REPORTED_FINDINGS_TITLE = 'Reported repeat expansions and Paraphase findings';

const ReportedFindings: React.FC<{
  findings: ReportedFinding[];
  /** What could not be read, in words ("the repeat calls"); the section says so. */
  unavailable?: string[];
}> = ({ findings, unavailable = [] }) => (
  <section className="surface-card report-findings" aria-label={REPORTED_FINDINGS_TITLE}>
    <h2 className="section-title">{REPORTED_FINDINGS_TITLE}</h2>
    {unavailable.length ? (
      <p className="report-paragraph" role="alert">
        Could not be loaded: {unavailable.join(' and ')}. Findings marked Report there are not listed.
      </p>
    ) : null}
    {findings.length === 0 ? (
      <p className="report-paragraph">No repeat locus or Paraphase region is marked Report.</p>
    ) : (
      <ul className="report-findings-list">
        {findings.map((finding) => (
          <li key={`${finding.kind}:${finding.key}`} className="report-finding">
            <p className="report-finding-head">
              <strong>{finding.gene}</strong> <span className="table-subtle">({NOUN[finding.kind]})</span>
              {finding.disease ? <> — {finding.disease}</> : null}
            </p>
            <p className="report-paragraph">
              Analyst&apos;s classification: <strong>{finding.classification ?? 'none'}</strong>
              {finding.differs ? <span className="report-finding-mark"> · differs from CoGA&apos;s status</span> : null}
              {finding.drift ? (
                <span className="report-finding-mark">
                  {' '}
                  · {finding.drift.label}
                  {finding.drift.changes.length ? `: ${finding.drift.changes.join('; ')}` : ''}
                </span>
              ) : null}
            </p>
            {finding.calls.length ? (
              <ul className="report-finding-calls">
                {finding.calls.map((call) => (
                  <li key={call.sample}>
                    {call.sample}: {call.call ? `${call.call} — ` : ''}
                    {call.status}
                  </li>
                ))}
              </ul>
            ) : null}
            {finding.note ? <p className="report-paragraph report-note">{finding.note}</p> : null}
          </li>
        ))}
      </ul>
    )}
  </section>
);

export default ReportedFindings;
