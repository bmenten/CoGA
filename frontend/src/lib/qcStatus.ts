import type { QcStatus } from './apiTypes';

/**
 * The one QC verdict vocabulary, shared by sample-integrity QC, sequencing QC and
 * mitochondrial QC.
 *
 * The wire values are terse (`warn`, `skip`) because they are also the backend's
 * `Literal` and CSS-class suffixes; what a reviewer reads is the label below. Keeping
 * both in one place is the point — the mtDNA workspace previously rendered its raw
 * status, so the same verdict appeared as "warning" there and "Warning" on the
 * sample-QC page, off a different backend spelling.
 */
export const QC_STATUS_LABEL: Record<QcStatus, string> = {
  pass: 'Pass',
  warn: 'Warning',
  /** Not `Error`: the app-wide term for a breached hard limit is a failed check. */
  fail: 'Fail',
  /** Not assessed — nothing to measure, or no acceptance limit configured. */
  skip: 'Not run',
};

/** Severity order, for rolling several checks up to a single worst verdict. */
export const QC_STATUS_RANK: Record<QcStatus, number> = {
  skip: 0,
  pass: 1,
  warn: 2,
  fail: 3,
};

export const worstQcStatus = (statuses: QcStatus[]): QcStatus =>
  statuses.reduce<QcStatus>(
    (worst, status) => (QC_STATUS_RANK[status] > QC_STATUS_RANK[worst] ? status : worst),
    'skip',
  );

/**
 * The sample-integrity roll-up. A Sample QC check that could not run (`skip`, such as a sex
 * check without chrX genotypes) confirmed nothing about the sample, so it counts as a
 * warning: never a pass beside checks that passed. Only no check at all stays `skip`. The
 * backend rolls the overall verdict up the same way (`sample_integrity_qc._overall_status`).
 *
 * `worstQcStatus` keeps the plain order, in which `skip` ranks lowest: for sequencing QC it
 * means nothing to measure or no limit configured, which is not a sample-identity question.
 */
export const worstIntegrityStatus = (statuses: QcStatus[]): QcStatus =>
  statuses.length
    ? worstQcStatus(statuses.map((status) => (status === 'skip' ? 'warn' : status)))
    : 'skip';

/** Shared chip styling, so one verdict looks the same wherever it is shown. */
export const QC_STATUS_CHIP: Record<QcStatus, string> = {
  pass: 'table-chip table-chip--success',
  warn: 'table-chip table-chip--warning',
  fail: 'table-chip table-chip--critical',
  skip: 'table-chip table-chip--neutral',
};
