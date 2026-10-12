import type { ApiClinicalAudit, ApiClinicalAuditEvent } from '../../lib/apiTypes';

/** A family's clinical audit trail as far as the report has read it. */
export type ClinicalAuditTrail = {
  /** The events read so far, most recent first. */
  events: ApiClinicalAuditEvent[];
  /** How many events the trail holds, as its first page counted them. */
  total: number;
};

/**
 * The trail the pages read so far hold. The API serves it a page at a time, most recent
 * first; each later page holds the events recorded before the last one shown, so together
 * the pages are the trail the first page counted. A page that came back without its count
 * counts what it holds.
 */
export function clinicalAuditTrail(
  pages: readonly (ApiClinicalAudit | null | undefined)[] | null | undefined,
): ClinicalAuditTrail {
  const events = (pages ?? []).flatMap((page) => page?.events ?? []);
  const counted = pages?.[0]?.total;
  return { events, total: Math.max(typeof counted === 'number' ? counted : 0, events.length) };
}

const mostRecent = (shown: number) =>
  shown === 1 ? 'the most recent' : `the ${shown.toLocaleString()} most recent`;

/**
 * What the audit trail says, on screen and in print, when it shows only its most recent
 * events; null when it shows them all.
 */
export function auditTrailShownNotice({ events, total }: ClinicalAuditTrail): string | null {
  return events.length < total
    ? `Showing ${mostRecent(events.length)} of ${total.toLocaleString()} events; the older ones are not shown.`
    : null;
}

/**
 * The sentence that heads every printed page of the report when its audit trail shows only
 * its most recent events, so a printout cannot pass for the whole trail; null when it shows
 * them all.
 */
export function auditTrailPrintNotice({ events, total }: ClinicalAuditTrail): string | null {
  return events.length < total
    ? `Incomplete — the audit trail lists ${mostRecent(events.length)} of its ${total.toLocaleString()} events; the older ones are not shown.`
    : null;
}
