import React from 'react';

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

/**
 * The corrections made in CoGA to the delivered package the family's last import came from, as
 * the family record keeps them (`metadata.package_import.delivery.corrections`, written by the
 * approval of a corrected delivery); none for a family not imported from a delivery, or from one
 * not corrected.
 */
export const intakeCorrectionsFromMetadata = (
  metadata: unknown
): IntakeCorrection[] => {
  if (!isObject(metadata) || !isObject(metadata.package_import)) return [];
  const delivery = metadata.package_import.delivery;
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

const shown = (value: unknown): string =>
  value === null || value === undefined
    ? 'not set'
    : typeof value === 'string'
      ? value
      : JSON.stringify(value);

/**
 * Says that the package the family was imported from was corrected in CoGA before its import, and
 * lists each correction: what changed, why, by whom and when. A change to a member's sex or
 * parents, which the pipeline used, is marked. The signed record keeps the same list.
 */
const IntakeCorrectionsNotice: React.FC<{ metadata?: unknown }> = ({
  metadata,
}) => {
  const corrections = intakeCorrectionsFromMetadata(metadata);
  if (!corrections.length) return null;
  const pedigree = corrections.some((correction) =>
    correction.changes.some((change) => change.kind === 'pipeline_used')
  );
  return (
    <details className="status-note">
      <summary>
        <strong>Corrected at intake.</strong> The delivered package this family
        was imported from was corrected in CoGA before its import (
        {corrections.length} correction{corrections.length === 1 ? '' : 's'}
        {pedigree ? ', the pedigree or a sex among them' : ''}).
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
