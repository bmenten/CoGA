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

/** Shared chip styling, so one verdict looks the same wherever it is shown. */
export const QC_STATUS_CHIP: Record<QcStatus, string> = {
  pass: 'table-chip table-chip--success',
  warn: 'table-chip table-chip--warning',
  fail: 'table-chip table-chip--critical',
  skip: 'table-chip table-chip--neutral',
};
