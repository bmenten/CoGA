import React from 'react';

import { analysisDataWords } from '../lib/familyAnalyses';
import { formatTimestamp } from '../lib/format';

interface IntakeChange {
  path: string;
  kind: string | null;
  before: unknown;
  after: unknown;
}

interface IntakeCorrection {
  revision: number | null;
  reason: string | null;
  by: string | null;
  at: string | null;
  changes: IntakeChange[];
}

const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value: unknown): string | null =>
  typeof value === 'string' && value ? value : null;

/** The corrections made in CoGA to the delivery one of the family's analyses was imported from. */
export interface CorrectedDelivery {
  /** The analysis (the key of the family's package record). */
  analysisType: string;
  corrections: IntakeCorrection[];
}

/**
 * For each analysis the family was imported for (`metadata.package_imports`: one package record
 * per analysis, such as the monogenic and the conventional NIPT of each pregnancy), in the order of the
 * analyses, the corrections made in CoGA to the delivered package its last import came from, as
 * the record keeps them (`delivery.corrections`, written by the approval of a corrected
 * delivery); none for an analysis not imported from a delivery, or from one not corrected.
 */
export const intakeCorrectionsFromMetadata = (
  metadata: unknown
): CorrectedDelivery[] => {
  if (!isObject(metadata) || !isObject(metadata.package_imports)) return [];
  const records = metadata.package_imports;
  return Object.keys(records)
    .sort()
    .map((analysisType) => {
      const record = records[analysisType];
      return {
        analysisType,
        corrections: correctionsOf(isObject(record) ? record.delivery : null),
      };
    })
    .filter((delivery) => delivery.corrections.length > 0);
};

const correctionsOf = (delivery: unknown): IntakeCorrection[] => {
  if (!isObject(delivery) || !Array.isArray(delivery.corrections)) return [];
  return delivery.corrections.filter(isObject).map((correction) => ({
    revision:
      typeof correction.revision === 'number' ? correction.revision : null,
    reason: text(correction.reason),
    by: text(correction.by),
    at: text(correction.at),
    changes: (Array.isArray(correction.changes) ? correction.changes : [])
      .filter(isObject)
      .map((change) => ({
        path: Array.isArray(change.path)
          ? change.path.map(String).join('.')
          : (text(change.path) ?? 'a field'),
        kind: text(change.kind),
        before: change.before ?? null,
        after: change.after ?? null,
      })),
  }));
};

/**
 * A member added (`added`) or removed (`removed`) at intake, which the change holds whole as its
 * value after or before; null for a change of a field.
 */
export const memberAddedOrRemoved = (change: {
  before: unknown;
  after: unknown;
}): 'added' | 'removed' | null => {
  const isMember = (value: unknown) =>
    isObject(value) && typeof value.sample_id === 'string';
  if (isMember(change.after) && !change.before) return 'added';
  if (isMember(change.before) && !change.after) return 'removed';
  return null;
};

const shown = (value: unknown): string =>
  value === null || value === undefined
    ? 'not set'
    : typeof value === 'string'
      ? value
      : JSON.stringify(value);

/**
 * Says that the package the family was imported from was corrected in CoGA before its import, and
 * lists each correction: what changed, why, by whom and when. A change to a member's sex or
 * parents, which the pipeline used, and a member added or removed are marked. The signed record
 * keeps the same list.
 */
const IntakeCorrectionsNotice: React.FC<{ metadata?: unknown }> = ({
  metadata,
}) => (
  <>
    {intakeCorrectionsFromMetadata(metadata).map((delivery) => (
      <CorrectedDeliveryNotice key={delivery.analysisType} delivery={delivery} />
    ))}
  </>
);

/** The corrections of one analysis's delivery, named by its analysis. */
const CorrectedDeliveryNotice: React.FC<{ delivery: CorrectedDelivery }> = ({
  delivery,
}) => {
  const { corrections } = delivery;
  const words = analysisDataWords(delivery.analysisType) ?? 'this family';
  const pedigree = corrections.some((correction) =>
    correction.changes.some(
      (change) =>
        change.kind === 'pipeline_used' || memberAddedOrRemoved(change) !== null
    )
  );
  return (
    <details className="status-note">
      <summary>
        <strong>Corrected at intake.</strong> The delivered package {words} was
        imported from was corrected in CoGA before its import (
        {corrections.length} correction{corrections.length === 1 ? '' : 's'}
        {pedigree ? ', the pedigree, a member or a sex among them' : ''}).
      </summary>
      <ol>
        {corrections.map((correction, index) => (
          <li key={`${correction.revision ?? 'r'}-${index}`}>
            Revision {correction.revision ?? '?'}
            {correction.by ? ` by ${correction.by}` : ''}
            {correction.at ? `, ${formatTimestamp(correction.at)}` : ''}:{' '}
            {correction.reason ?? 'no reason recorded'}
            <ul>
              {correction.changes.map((change, changeIndex) => (
                <li key={`${change.path}-${changeIndex}`}>
                  <code>{change.path}</code>: {shown(change.before)} →{' '}
                  {shown(change.after)}
                  {change.kind === 'pipeline_used'
                    ? ' (the pipeline used it)'
                    : ''}
                  {memberAddedOrRemoved(change)
                    ? ` (a member ${memberAddedOrRemoved(change)})`
                    : ''}
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </details>
  );
};

export default IntakeCorrectionsNotice;
