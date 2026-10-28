import React from 'react';

/**
 * Each family member's call on one finding, the same grid on every variant card: the
 * member (sex, sample ID, role), the call itself in bold monospace, the measurements behind
 * it right-aligned, and a status at the end where the kind of finding has one.
 *
 *   small variant   GT          DP · AD · AF
 *   repeat          alleles     — · status
 *   Paraphase       total CN    gene CN … · status
 *   mtDNA           heteroplasmy  DP · AD · zygosity
 *
 * One tone vocabulary colours the call (and the status chip) on every page: `alert` red
 * (pathogenic, homozygous, homoplasmic), `warn` amber (heterozygous, grey zone, carrier,
 * heteroplasmic), `info` blue (not assessed), `review` violet (a call to look at again, its
 * own colour, ENG-10), `ref` muted (reference, normal), `na` faded (no call or no data).
 */

export type GenotypeTone = 'alert' | 'warn' | 'info' | 'review' | 'ref' | 'na';

export interface GenotypeColumn {
  key: string;
  label: string;
  /** Right-aligned in tabular figures: a depth, a fraction, a count. */
  numeric?: boolean;
}

export interface GenotypeMember {
  sample_id: string;
  role?: string | null;
  sex?: string | null;
  affected?: boolean | null;
}

export interface GenotypeRow {
  member: GenotypeMember;
  tone: GenotypeTone;
  /** The call, shown in the first value column. */
  call: React.ReactNode;
  /** The other columns' values, by column key. */
  values?: Record<string, React.ReactNode>;
  status?: { label: string; tone: GenotypeTone } | null;
  /** Read under the row, in the value columns: an interruption, a note on the call. */
  detail?: React.ReactNode;
}

export type GenotypeGridEntry = GenotypeRow | { group: string; empty?: string };

const SEX_SYMBOL: Record<string, string> = { male: '♂', female: '♀' };

export const STATUS_CHIP_TONE_CLASS: Record<GenotypeTone, string> = {
  alert: 'critical',
  warn: 'warning',
  info: 'impact',
  review: 'review',
  ref: 'neutral',
  na: 'neutral',
};

const FamilyGenotypeGrid: React.FC<{
  /** The call's column heading: GT, Alleles, Total CN, Heteroplasmy. */
  callLabel: string;
  columns?: GenotypeColumn[];
  entries: GenotypeGridEntry[];
  /** Whether the grid ends with a status column. */
  withStatus?: boolean;
  className?: string;
}> = ({ callLabel, columns = [], entries, withStatus = false, className }) => {
  const columnCount = 3 + columns.length + (withStatus ? 1 : 0);
  return (
    <div
      className={`variant-card-gtlist${className ? ` ${className}` : ''}`}
      style={{ '--gt-cols': columnCount - 1 } as React.CSSProperties}
    >
      <div className="variant-card-gthead">
        <span>Sample</span>
        <span>Role</span>
        <span>{callLabel}</span>
        {columns.map((column) => (
          <span key={column.key} className={column.numeric ? 'variant-card-gthead-num' : undefined}>
            {column.label}
          </span>
        ))}
        {withStatus ? <span>Status</span> : null}
      </div>
      {entries.map((entry) => {
        if ('group' in entry) {
          return (
            <React.Fragment key={`group-${entry.group}`}>
              <div className="variant-card-gtgroup">{entry.group}</div>
              {entry.empty ? <div className="variant-card-gtgroup-empty">{entry.empty}</div> : null}
            </React.Fragment>
          );
        }
        const { member } = entry;
        const sex = (member.sex || '').toLowerCase();
        return (
          <React.Fragment key={member.sample_id}>
            <div
              className={`variant-card-gtline variant-card-gtline--${entry.tone}${
                member.affected ? ' is-affected' : ''
              }${member.role === 'proband' ? ' is-proband' : ''}`}
            >
              <span className="variant-card-gtline-sample">
                <span
                  className={`variant-card-gtline-sex variant-card-gtline-sex--${
                    SEX_SYMBOL[sex] ? sex : 'und'
                  }`}
                  title={member.sex || 'unknown sex'}
                >
                  {SEX_SYMBOL[sex] ?? '⚥'}
                </span>
                {member.sample_id}
              </span>
              <span className="variant-card-gtline-role">{member.role || '—'}</span>
              <span className="variant-card-gtline-gt">{entry.call}</span>
              {columns.map((column) => (
                <span
                  key={column.key}
                  className={column.numeric ? 'variant-card-gtline-metric' : 'variant-card-gtline-value'}
                >
                  {entry.values?.[column.key] ?? '—'}
                </span>
              ))}
              {withStatus ? (
                <span className="variant-card-gtline-status">
                  {entry.status ? (
                    <span
                      className={`variant-card-chip variant-card-chip--${STATUS_CHIP_TONE_CLASS[entry.status.tone]}`}
                    >
                      {entry.status.label}
                    </span>
                  ) : null}
                </span>
              ) : null}
            </div>
            {entry.detail ? <div className="variant-card-gtline-detail">{entry.detail}</div> : null}
          </React.Fragment>
        );
      })}
    </div>
  );
};

export default FamilyGenotypeGrid;
