import React from 'react';
import * as d3 from 'd3';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import type {
  NiptChromosomeTransmissionOut,
  NiptStatisticsGroupOut,
  NiptVariantStatisticsOut,
} from '../../lib/apiSchema.generated';
import type { QcStatus } from '../../lib/apiTypes';
import { QC_STATUS_CHIP, QC_STATUS_LABEL } from '../../lib/qcStatus';
import {
  CATEGORY_LABELS,
  FATHER_STATE_LABELS,
  niptFetalSexLabel,
  pct,
} from './niptClassification';
import { NIPT_QUALITY_FAILURE_LABELS } from './NiptQcPanel';
import { VariantSummarySectionState } from './VariantSummarySectionState';
import VizTooltip from '../../components/visualizations/VizTooltip';

/**
 * The variant summary's NIPT-M tab: a monogenic NIPT family's plasma sites as its NIPT analysis
 * classes them (the NIPT page's analysis: the same fetal fraction, filters and categories). The
 * plasma's GT says nothing of a genotype (Mutect2 writes 0/1 on every call, and the plasma is the
 * mother's and the fetus's reads together), so the genotypes here come from the allele
 * fractions. Descriptive: the signed record does not hold these figures.
 */

type Genotype = 'hom_ref' | 'het' | 'hom_alt';
type Split = 'none' | 'father' | 'variant_class';

const GENOTYPES: Genotype[] = ['hom_ref', 'het', 'hom_alt'];
const GENOTYPE_LABEL: Record<Genotype, string> = {
  hom_ref: 'Hom ref',
  het: 'Het',
  hom_alt: 'Hom alt',
};
export const PATERNAL_NOT_TRANSMITTED = 'paternal_not_transmitted';

export const groupLabel = (group: Pick<NiptStatisticsGroupOut, 'key' | 'category'>): string =>
  group.category != null
    ? `${group.category} · ${CATEGORY_LABELS[group.category] ?? `Category ${group.category}`}`
    : "Father's het allele, not inherited";

// One colour per group (tokens.css, --color-nipt-*), so the matrix's swatches and the
// histogram's bars name the same group.
const GROUP_COLOR: Record<string, string> = {
  '1': 'var(--color-nipt-cat-1)',
  '2': 'var(--color-nipt-cat-2)',
  '3': 'var(--color-nipt-cat-3)',
  '4': 'var(--color-nipt-cat-4)',
  '5': 'var(--color-nipt-cat-5)',
  '6': 'var(--color-nipt-cat-6)',
  '7': 'var(--color-nipt-cat-7)',
  '8': 'var(--color-nipt-cat-8)',
  [PATERNAL_NOT_TRANSMITTED]: 'var(--color-nipt-not-transmitted)',
};
const groupColor = (key: string): string => GROUP_COLOR[key] ?? 'var(--color-gray)';

// The father's genotype as the analysis reads it (trusted_father_state).
const TRUSTED_FATHER_LABEL: Record<string, string> = {
  hom_ref: 'father ref',
  het: 'father het',
  hom_alt: 'father hom alt',
  missing: 'father uncertain',
};

const NOT_CLASSIFIED_LABEL: Record<string, string> = {
  sex_chromosome: 'on a sex chromosome (the fetal sex reads chrX)',
  no_plasma_depth: 'no plasma depth known',
  low_plasma_depth: 'plasma depth too low to see a missing allele',
  undetectable_at_fetal_fraction: 'a paternal allele too faint to see at this fetal fraction',
  no_alt_signal: 'no allele in the plasma and none in the father',
  other: 'other',
};

const formatCount = (value: number): string => value.toLocaleString('en-US');
const share = (part: number, whole: number): string =>
  whole ? `${((part / whole) * 100).toFixed(1)}%` : '—';
const vaf = (value?: number | null): string => (value == null ? '—' : value.toFixed(3));
const ratio = (part: number, whole: number): string =>
  whole ? `${share(part, whole)} (${formatCount(part)} of ${formatCount(whole)})` : '—';

const splitText = (group: NiptStatisticsGroupOut, split: Split): string | null => {
  const entries =
    split === 'father'
      ? Object.entries(group.by_father ?? {}).map(
          ([state, count]) => [TRUSTED_FATHER_LABEL[state] ?? state, count] as const
        )
      : split === 'variant_class'
        ? Object.entries(group.by_variant_class ?? {})
        : [];
  if (!entries.length) return null;
  return [...entries]
    .sort((a, b) => b[1] - a[1])
    .map(([label, count]) => `${label} ${formatCount(count)}`)
    .join(' · ');
};

const MOTHER_LABEL: Record<Genotype, string> = {
  hom_ref: 'Mother hom ref',
  het: 'Mother het',
  hom_alt: 'Mother hom alt',
};
const SPLIT_HEADING: Record<Exclude<Split, 'none'>, string> = {
  father: 'By the father’s genotype',
  variant_class: 'By variant class',
};
const SPLITS: [Split, string][] = [
  ['none', 'Nothing'],
  ['father', "Father's genotype"],
  ['variant_class', 'Variant class'],
];

const Swatch: React.FC<{ groupKey: string }> = ({ groupKey }) => (
  <span className="nipt-swatch" style={{ background: groupColor(groupKey) }} aria-hidden="true" />
);

/** A share as a number beside a thin bar of the group's colour. */
const ShareBar: React.FC<{ part: number; whole: number; groupKey?: string }> = ({
  part,
  whole,
  groupKey,
}) => (
  <span className="nipt-share">
    <span className="nipt-share-track" aria-hidden="true">
      <span
        className="nipt-share-fill"
        style={{
          width: `${whole ? Math.min(100, (part / whole) * 100) : 0}%`,
          background: groupKey ? groupColor(groupKey) : 'var(--color-secondary)',
        }}
      />
    </span>
    <span className="nipt-share-value">{share(part, whole)}</span>
  </span>
);

/** The groups one under another: per mother's genotype, each fetal genotype's groups, with
 *  their count, share, expected and observed plasma allele fraction and confidence. */
export const GroupTable: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const [split, setSplit] = React.useState<Split>('none');
  const total = stats.classified_sites;
  const columns = split === 'none' ? 7 : 8;
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <span className="stat-label">Split each group by</span>
        {SPLITS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`variant-quick-toggle${split === key ? ' variant-quick-toggle--active' : ''}`}
            aria-pressed={split === key}
            onClick={() => setSplit(key)}
          >
            {label}
          </button>
        ))}
      </div>
      <div className="analysis-results-card overflow-x-auto">
        <table
          className="analysis-table table-sticky nipt-group-table"
          aria-label="Plasma sites by the mother's and the fetus's genotype"
        >
          <thead>
            <tr>
              <th scope="col">Fetus</th>
              <th scope="col">Group</th>
              <th scope="col" className="nipt-num">
                Sites
              </th>
              <th scope="col">Share</th>
              <th scope="col" className="nipt-num">
                Expected VAF
              </th>
              <th scope="col" className="nipt-num">
                Observed VAF
              </th>
              <th scope="col" className="nipt-num">
                Confidence ≥ 0.99
              </th>
              {split !== 'none' ? <th scope="col">{SPLIT_HEADING[split]}</th> : null}
            </tr>
          </thead>
          <tbody>
            {GENOTYPES.map((mother) => {
              const rows = stats.groups
                .filter((group) => group.maternal_genotype === mother)
                .sort(
                  (a, b) => GENOTYPES.indexOf(a.fetal_genotype) - GENOTYPES.indexOf(b.fetal_genotype)
                );
              if (!rows.length) return null;
              const sites = rows.reduce((sum, group) => sum + group.sites, 0);
              return (
                <React.Fragment key={mother}>
                  <tr className="variant-summary-group-row">
                    <td colSpan={2}>{MOTHER_LABEL[mother]}</td>
                    <td className="nipt-num">{formatCount(sites)}</td>
                    <td>
                      <ShareBar part={sites} whole={total} />
                    </td>
                    <td colSpan={columns - 4} />
                  </tr>
                  {rows.map((group) => (
                    <tr key={group.key}>
                      <td className="nipt-group-fetus">{GENOTYPE_LABEL[group.fetal_genotype]}</td>
                      <td>
                        <span className="nipt-group-name">
                          <Swatch groupKey={group.key} />
                          {groupLabel(group)}
                        </span>
                      </td>
                      <td className="nipt-num">{formatCount(group.sites)}</td>
                      <td>
                        <ShareBar part={group.sites} whole={total} groupKey={group.key} />
                      </td>
                      <td className="nipt-num">{vaf(group.expected_vaf)}</td>
                      <td className="nipt-num">{vaf(group.observed_median_vaf)}</td>
                      <td className="nipt-num">
                        {group.sites ? share(group.high_confidence, group.sites) : '—'}
                      </td>
                      {split !== 'none' ? (
                        <td className="table-subtle">{splitText(group, split) ?? '—'}</td>
                      ) : null}
                    </tr>
                  ))}
                </React.Fragment>
              );
            })}
            <tr className="variant-summary-group-row">
              <td colSpan={2}>All groups</td>
              <td className="nipt-num">{formatCount(total)}</td>
              <td colSpan={columns - 3} />
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};

const CHART = { width: 760, height: 280, left: 56, right: 16, top: 30, bottom: 46 };

/** The expected allele fractions drawn as dashed lines, the categories that share one (de novo
 *  and paternal, at FF/2) named together. */
const expectedMarks = (groups: NiptStatisticsGroupOut[]) => {
  const marks = new Map<string, { at: number; categories: number[] }>();
  groups.forEach((group) => {
    if (group.expected_vaf == null || group.category == null || !group.sites) return;
    const key = group.expected_vaf.toFixed(3);
    const mark = marks.get(key) ?? { at: group.expected_vaf, categories: [] };
    mark.categories.push(group.category);
    marks.set(key, mark);
  });
  return [...marks.values()].sort((a, b) => a.at - b.at);
};

/** The plasma allele fractions, stacked by group, with a dashed line where each category is
 *  expected at the family's fetal fraction; hovering a bin lists its sites by group. */
export const AlleleFractionHistogram: React.FC<{ stats: NiptVariantStatisticsOut }> = ({
  stats,
}) => {
  const [logScale, setLogScale] = React.useState(false);
  const [hover, setHover] = React.useState<{ bin: number; x: number; y: number } | null>(null);
  const groups = stats.groups.filter((group) => group.vaf_histogram.some((count) => count > 0));
  const bins = Math.max(0, ...stats.groups.map((group) => group.vaf_histogram.length));
  const binWidth = stats.vaf_bin_width;
  const totals = Array.from({ length: bins }, (_, bin) =>
    groups.reduce((sum, group) => sum + (group.vaf_histogram[bin] ?? 0), 0)
  );
  const sites = totals.reduce((sum, count) => sum + count, 0);
  const { width, height, left, right, top, bottom } = CHART;
  const peak = Math.max(1, ...totals);
  const x = d3.scaleLinear().domain([0, 1]).range([left, width - right]);
  const y = logScale
    ? d3.scaleSymlog().constant(1).domain([0, peak]).range([height - bottom, top])
    : d3.scaleLinear().domain([0, peak]).nice().range([height - bottom, top]);
  const yTicks = logScale
    ? [0, 1, 10, 100, 1000, 10000, 100000].filter((tick) => tick <= peak)
    : y.ticks(5).filter((tick) => Number.isInteger(tick));
  const barWidth = Math.max(x(binWidth) - x(0) - 1, 0.5);
  const marks = expectedMarks(stats.groups);
  const hovered = hover ? hover.bin : null;
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <div className="nipt-legend">
          {groups.map((group) => (
            <span key={group.key} className="nipt-legend-item">
              <Swatch groupKey={group.key} />
              {groupLabel(group)}
            </span>
          ))}
        </div>
        <button type="button" onClick={() => setLogScale((value) => !value)} className="button-ghost">
          {logScale ? 'Linear scale' : 'Log scale'}
        </button>
      </div>
      <div className="viz-panel nipt-vaf-scroll">
        <svg
          className="nipt-vaf-chart"
          viewBox={`0 0 ${width} ${height}`}
          role="img"
          aria-label={`Plasma allele fractions of ${formatCount(sites)} sites in bins of ${binWidth}, stacked by group, with the expected fraction of each category at the fetal fraction`}
          onMouseLeave={() => setHover(null)}
        >
          {yTicks.map((tick) => (
            <g key={`y-${tick}`}>
              <line className="nipt-vaf-grid" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />
              <text x={left - 8} y={y(tick) + 4} textAnchor="end">
                {formatCount(tick)}
              </text>
            </g>
          ))}
          {hovered != null ? (
            <rect
              className="nipt-vaf-hover"
              x={x(hovered * binWidth)}
              width={x(binWidth) - x(0)}
              y={top}
              height={height - bottom - top}
            />
          ) : null}
          {totals.map((_total, bin) => {
            let base = 0;
            return groups.map((group) => {
              const count = group.vaf_histogram[bin] ?? 0;
              if (!count) return null;
              const lower = base;
              base += count;
              return (
                <rect
                  key={`${group.key}-${bin}`}
                  data-bin={bin}
                  data-group={group.key}
                  x={x(bin * binWidth) + 0.5}
                  width={barWidth}
                  y={y(base)}
                  height={Math.max(y(lower) - y(base), 0)}
                  fill={groupColor(group.key)}
                />
              );
            });
          })}
          {marks.map((mark) => (
            <g key={`expected-${mark.at}`} className="nipt-vaf-expected">
              <line x1={x(mark.at)} x2={x(mark.at)} y1={top - 4} y2={height - bottom} />
              <text className="nipt-vaf-expected-label" x={x(mark.at)} y={top - 10} textAnchor="middle">
                {mark.categories.join('/')}
              </text>
            </g>
          ))}
          <line className="nipt-vaf-axis" x1={left} x2={width - right} y1={height - bottom} y2={height - bottom} />
          {d3.range(0, 11).map((step) => {
            const tick = step / 10;
            return (
              <g key={`x-${step}`}>
                <line className="nipt-vaf-axis" x1={x(tick)} x2={x(tick)} y1={height - bottom} y2={height - bottom + 4} />
                <text x={x(tick)} y={height - bottom + 17} textAnchor="middle">
                  {tick.toFixed(1)}
                </text>
              </g>
            );
          })}
          <text className="nipt-vaf-axis-title" x={left + (width - left - right) / 2} y={height - 8} textAnchor="middle">
            Plasma allele fraction
          </text>
          <text
            className="nipt-vaf-axis-title"
            transform={`translate(14 ${top + (height - bottom - top) / 2}) rotate(-90)`}
            textAnchor="middle"
          >
            {logScale ? 'Sites (log scale)' : 'Sites'}
          </text>
          {totals.map((_total, bin) => (
            <rect
              key={`hit-${bin}`}
              className="nipt-vaf-hit"
              x={x(bin * binWidth)}
              width={x(binWidth) - x(0)}
              y={top}
              height={height - bottom - top}
              onMouseMove={(event) => setHover({ bin, x: event.clientX, y: event.clientY })}
            />
          ))}
        </svg>
      </div>
      {hover && totals[hover.bin] ? (
        <VizTooltip x={hover.x} y={hover.y}>
          <p className="font-semibold">
            {(hover.bin * binWidth).toFixed(2)}–{((hover.bin + 1) * binWidth).toFixed(2)} ·{' '}
            {formatCount(totals[hover.bin])} sites
          </p>
          {groups
            .filter((group) => group.vaf_histogram[hover.bin])
            .map((group) => (
              <p key={group.key} className="nipt-tip-row">
                <span className="nipt-group-name">
                  <Swatch groupKey={group.key} />
                  {groupLabel(group)}
                </span>
                <span>{formatCount(group.vaf_histogram[hover.bin])}</span>
              </p>
            ))}
        </VizTooltip>
      ) : null}
    </div>
  );
};

const MARK_LABEL: Record<string, string> = {
  no_paternal_alleles: 'No paternal allele',
  no_maternal_alleles: 'No maternal allele',
};

const sumRows = (rows: NiptChromosomeTransmissionOut[]) =>
  rows.reduce(
    (sum, row) => ({
      paternalHet: sum.paternalHet + row.paternal_het_transmitted,
      paternalHetAll: sum.paternalHetAll + row.paternal_het_transmitted + row.paternal_het_not_transmitted,
      paternalHom: sum.paternalHom + row.paternal_hom_alt_transmitted,
      paternalHomAll: sum.paternalHomAll + row.paternal_hom_alt_transmitted + row.paternal_hom_alt_not_transmitted,
      maternal: sum.maternal + row.maternal_het_inherited,
      maternalAll: sum.maternalAll + row.maternal_het_inherited + row.maternal_het_not_inherited,
      fetalHomAlt: sum.fetalHomAlt + row.fetal_hom_alt,
    }),
    { paternalHet: 0, paternalHetAll: 0, paternalHom: 0, paternalHomAll: 0, maternal: 0, maternalAll: 0, fetalHomAlt: 0 }
  );

export const TransmissionTable: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const totals = sumRows(stats.chromosomes);
  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky" aria-label="Transmission by autosome">
        <thead>
          <tr>
            <th>Chromosome</th>
            <th className="nipt-num">Father’s het alleles inherited</th>
            <th className="nipt-num">Father’s hom-alt alleles inherited</th>
            <th className="nipt-num">Mother’s het alleles inherited</th>
            <th className="nipt-num">Fetus hom alt</th>
            <th>Marked</th>
          </tr>
        </thead>
        <tbody>
          {stats.chromosomes.map((row) => (
            <tr key={row.chrom} className={row.marks.length ? 'nipt-transmission-marked' : undefined}>
              <td>{row.chrom}</td>
              <td className="nipt-num">
                {ratio(row.paternal_het_transmitted, row.paternal_het_transmitted + row.paternal_het_not_transmitted)}
              </td>
              <td className="nipt-num">
                {ratio(row.paternal_hom_alt_transmitted, row.paternal_hom_alt_transmitted + row.paternal_hom_alt_not_transmitted)}
              </td>
              <td className="nipt-num">
                {ratio(row.maternal_het_inherited, row.maternal_het_inherited + row.maternal_het_not_inherited)}
              </td>
              <td className="nipt-num">{formatCount(row.fetal_hom_alt)}</td>
              <td>
                {row.marks.map((mark) => (
                  <span key={mark} className="table-chip table-chip--warning">
                    {MARK_LABEL[mark] ?? mark}
                  </span>
                ))}
              </td>
            </tr>
          ))}
          <tr className="variant-summary-group-row">
            <td>Autosomes</td>
            <td className="nipt-num">{ratio(totals.paternalHet, totals.paternalHetAll)}</td>
            <td className="nipt-num">{ratio(totals.paternalHom, totals.paternalHomAll)}</td>
            <td className="nipt-num">{ratio(totals.maternal, totals.maternalAll)}</td>
            <td className="nipt-num">{formatCount(totals.fetalHomAlt)}</td>
            <td />
          </tr>
        </tbody>
      </table>
    </div>
  );
};

const CountTable: React.FC<{ title: string; rows: [React.ReactNode, React.ReactNode][] }> = ({
  title,
  rows,
}) => (
  <div className="viz-panel space-y-1">
    <h4 className="font-semibold">{title}</h4>
    <table className="analysis-table">
      <tbody>
        {rows.map(([label, value], index) => (
          <tr key={index}>
            <td>{label}</td>
            <td className="nipt-num">{value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  </div>
);

const FATHER_ORDER = ['hom_ref', 'absent', 'het', 'hom_alt', 'low_vaf', 'low_support', 'missing'];

export const CallsAndFather: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const filters = stats.filter_counts;
  const totalIn = filters.total_in ?? 0;
  const failures = Object.entries(stats.qc.quality_failures ?? {}).filter(([, count]) => count > 0);
  const callerPass = stats.caller_filters.PASS ?? 0;
  const callerOther = Object.entries(stats.caller_filters)
    .filter(([key]) => key !== 'PASS')
    .sort((a, b) => b[1] - a[1]);
  const father = Object.entries(stats.father_genotypes).sort(
    (a, b) => FATHER_ORDER.indexOf(a[0]) - FATHER_ORDER.indexOf(b[0])
  );
  const notClassified = Object.entries(stats.not_classified).sort((a, b) => b[1] - a[1]);
  return (
    <div className="variant-summary-grid">
      <CountTable
        title="Plasma calls"
        rows={[
          ['Plasma calls read', formatCount(totalIn)],
          ['Failed the quality filter', formatCount(filters.failed_quality ?? 0)],
          ...failures.map(
            ([reason, count]): [React.ReactNode, React.ReactNode] => [
              <span key={reason} className="nipt-failure-reason">
                {NIPT_QUALITY_FAILURE_LABELS[reason] ?? reason}
              </span>,
              formatCount(count),
            ]
          ),
          ['On the artefact list', formatCount(filters.failed_artifact ?? 0)],
          ['Passed', formatCount(filters.passed ?? 0)],
          ["Father's alleles without a plasma call", formatCount(filters.paternal_only ?? 0)],
          ['Sites in the groups above', formatCount(stats.classified_sites)],
          ...notClassified.map(
            ([reason, count]): [React.ReactNode, React.ReactNode] => [
              `Not grouped: ${NOT_CLASSIFIED_LABEL[reason] ?? reason}`,
              formatCount(count),
            ]
          ),
        ]}
      />
      <CountTable
        title="Caller FILTER of the plasma calls read"
        rows={[
          ['PASS', `${formatCount(callerPass)} (${share(callerPass, totalIn)})`],
          ...callerOther.map(
            ([value, count]): [React.ReactNode, React.ReactNode] => [
              value,
              `${formatCount(count)} (${share(count, totalIn)})`,
            ]
          ),
        ]}
      />
      <CountTable
        title="Father’s genotype at the sites read"
        rows={father.map(([state, count]): [React.ReactNode, React.ReactNode] => [
          FATHER_STATE_LABELS[state] ?? state,
          formatCount(count),
        ])}
      />
    </div>
  );
};

const Facts: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const ff = stats.fetal_fraction;
  const { paternity, fetal_sex: sex } = stats.qc;
  return (
    <div className="grid gap-3 md:grid-cols-4">
      <div className="stat-card">
        <span className="stat-label">Fetal fraction</span>
        <span className="stat-value">{pct(ff.ff_computed)}</span>
        <span className="table-subtle">
          {ff.ci_low != null && ff.ci_high != null ? `95% CI ${pct(ff.ci_low)}–${pct(ff.ci_high)} · ` : ''}
          {formatCount(ff.n_sites)} category-7 sites{' '}
        </span>
        <span className={QC_STATUS_CHIP[stats.qc.fetal_fraction_status as QcStatus]}>
          {QC_STATUS_LABEL[stats.qc.fetal_fraction_status as QcStatus]}
        </span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Sites grouped</span>
        <span className="stat-value">{formatCount(stats.classified_sites)}</span>
        <span className="table-subtle">
          of {formatCount(stats.filter_counts.total_in ?? 0)} plasma calls and{' '}
          {formatCount(stats.filter_counts.paternal_only ?? 0)} paternal-only sites
        </span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Fetal sex</span>
        <span className="stat-value">{niptFetalSexLabel(sex.call)}</span>
        <span className="table-subtle">
          paternal X seen at {formatCount(sex.x_transmitted)} of {formatCount(sex.informative_sites)} sites{' '}
        </span>
        <span className={QC_STATUS_CHIP[sex.status as QcStatus]}>{QC_STATUS_LABEL[sex.status as QcStatus]}</span>
      </div>
      <div className="stat-card">
        <span className="stat-label">Paternity</span>
        <span className="stat-value">{QC_STATUS_LABEL[paternity.status as QcStatus]}</span>
        <span className="table-subtle">
          hom-alt alleles inherited {formatCount(paternity.hom_alt_transmitted)} of{' '}
          {formatCount(paternity.hom_alt_transmitted + paternity.hom_alt_not_transmitted)}
        </span>
      </div>
    </div>
  );
};

const VariantSummaryNipt: React.FC<{ familyId: string; projectId?: string | null }> = ({
  familyId,
  projectId,
}) => {
  const query = useQuery<NiptVariantStatisticsOut>({
    queryKey: ['family', familyId, 'nipt-statistics', projectId || null],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/nipt/statistics`, {
        params: projectId ? { project_id: projectId } : undefined,
      });
      return res.data as NiptVariantStatisticsOut;
    },
  });
  const stats = query.data;
  return (
    <section id="nipt-m" className="analysis-panel space-y-4">
      <h2 className="section-title">NIPT-M</h2>
      {!stats ? (
        <VariantSummarySectionState
          what="NIPT-M statistics"
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : (
        <>
          <p className="analysis-count">
            The plasma’s sites as the NIPT analysis classes them, with the NIPT page’s fetal
            fraction, filters and categories. The plasma’s GT says nothing of a genotype (Mutect2
            writes 0/1 on every call, and the plasma holds the mother’s and the fetus’s DNA), so
            the genotypes here come from the allele fractions. These figures describe the
            analysis; the signed record does not hold them.
          </p>
          <Facts stats={stats} />

          <h3 className="font-semibold">Mother and fetus</h3>
          <p className="analysis-count">
            The groups one under another, by the mother’s genotype and then the fetus’s: each with
            its sites and share, the plasma allele fraction (VAF) its category predicts at this
            fetal fraction beside the median observed, and the share classified with a confidence
            of 0.99 or more. A low share for the mother’s het groups means the fetal fraction
            separates them poorly. A hom-ref mother with a hom-alt fetus, or a hom-alt mother with
            a hom-ref fetus, is not modelled.
          </p>
          <GroupTable stats={stats} />

          <h3 className="font-semibold">Plasma allele fraction</h3>
          <p className="analysis-count">
            Each group’s sites by their plasma allele fraction, in bins of{' '}
            {stats.vaf_bin_width}. The dashed lines, numbered by category, are where the categories
            are expected at this fetal fraction: peaks off their lines mean the fetal fraction or
            the classes do not fit the data. Hover a bin for its sites by group.
          </p>
          <AlleleFractionHistogram stats={stats} />

          <h3 className="font-semibold">Transmission by chromosome</h3>
          <p className="analysis-count">
            Per autosome, the father’s alleles the mother does not carry that the fetus
            inherited (the paternity check’s sites: about half of his het alleles, all his hom-alt
            ones), and the mother’s het alleles it inherited beside a confident hom-ref or hom-alt
            father (about half). An autosome where none of a parent’s alleles reached the fetus
            over at least {stats.chromosome_mark_min_sites} sites is marked: it can point to
            uniparental disomy or a deletion. These counts describe the calls; CoGA does not call
            UPD. chrX is read for the fetal sex.
          </p>
          {stats.chromosomes.length ? (
            <TransmissionTable stats={stats} />
          ) : (
            <p className="analysis-count">No autosomal site was read.</p>
          )}

          <h3 className="font-semibold">Calls and the father’s genotypes</h3>
          <p className="analysis-count">
            The plasma calls through the analysis’s quality filter and artefact list, and the
            caller’s own FILTER: for a NIPT pair, Mutect2’s somatic filters, which flag many
            germline calls (clustered_events, multiallelic, weak_evidence); the analysis does not
            use it as a gate. The father’s genotype is read from his allele fraction (het from
            0.20, hom alt from 0.80).
          </p>
          <CallsAndFather stats={stats} />
        </>
      )}
    </section>
  );
};

export default VariantSummaryNipt;
