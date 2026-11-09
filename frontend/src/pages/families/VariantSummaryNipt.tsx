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

type PlotView = 'groups' | 'all';
const PLOT_VIEWS: [PlotView, string][] = [
  ['groups', 'Per group'],
  ['all', 'All sites'],
];
const HOM_HOM = '6';
// The hom/hom peak is cut when it is more than this many times the tallest other bin.
const CUT_RATIO = 1.5;
const CHART = { width: 760, height: 280, left: 56, right: 16, top: 30, bottom: 46 };
const ROWS = { width: 760, labels: 200, right: 16, top: 8, row: 40, gap: 8, bottom: 46 };

const binRange = (bin: number, width: number): string =>
  `${(bin * width).toFixed(2)}–${((bin + 1) * width).toFixed(2)}`;

/** The groups the plot draws: those with a plasma call of their own (the father's missing
 *  alleles have none), in the order of the fraction their category expects (the paternal
 *  allele before a de novo one at the same FF/2, as the table lists them). */
const drawnGroups = (stats: NiptVariantStatisticsOut): NiptStatisticsGroupOut[] =>
  stats.groups
    .filter((group) => group.vaf_histogram.some((count) => count > 0))
    .sort((a, b) => (a.expected_vaf ?? 2) - (b.expected_vaf ?? 2));

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

/** The allele-fraction axis under a plot: ticks every 0.1 and its title. */
const FractionAxis: React.FC<{ x: d3.ScaleLinear<number, number>; at: number; height: number }> = ({
  x,
  at,
  height,
}) => (
  <g>
    <line className="nipt-vaf-axis" x1={x(0)} x2={x(1)} y1={at} y2={at} />
    {d3.range(0, 11).map((step) => {
      const tick = step / 10;
      return (
        <g key={`x-${step}`}>
          <line className="nipt-vaf-axis" x1={x(tick)} x2={x(tick)} y1={at} y2={at + 4} />
          <text x={x(tick)} y={at + 17} textAnchor="middle">
            {tick.toFixed(1)}
          </text>
        </g>
      );
    })}
    <text className="nipt-vaf-axis-title" x={(x(0) + x(1)) / 2} y={height - 8} textAnchor="middle">
      Plasma allele fraction
    </text>
  </g>
);

type Hover = { group?: string; bin: number; x: number; y: number };

/** One row per group, each scaled to its own tallest bin, on one allele-fraction axis: every
 *  group's spread around the line its category expects, whatever its size. */
const GroupRows: React.FC<{
  stats: NiptVariantStatisticsOut;
  groups: NiptStatisticsGroupOut[];
  onHover: (hover: Hover | null) => void;
  hover: Hover | null;
}> = ({ stats, groups, onHover, hover }) => {
  const { width, labels, right, top, row, gap, bottom } = ROWS;
  const height = top + groups.length * (row + gap) + bottom;
  const x = d3.scaleLinear().domain([0, 1]).range([labels + 12, width - right]);
  const binWidth = stats.vaf_bin_width;
  const step = x(binWidth) - x(0);
  const axisAt = top + groups.length * (row + gap) - gap / 2;
  return (
    <svg
      className="nipt-vaf-chart"
      viewBox={`0 0 ${width} ${height}`}
      role="img"
      aria-label={`Plasma allele fractions in bins of ${binWidth}, one row per group, each scaled to its own tallest bin, with the fraction its category expects at the fetal fraction`}
      onMouseLeave={() => onHover(null)}
    >
      {d3.range(0, 11).map((tenth) => (
        <line
          key={`grid-${tenth}`}
          className="nipt-vaf-grid"
          x1={x(tenth / 10)}
          x2={x(tenth / 10)}
          y1={top}
          y2={axisAt}
        />
      ))}
      {groups.map((group, index) => {
        const rowTop = top + index * (row + gap);
        const base = rowTop + row;
        const peak = Math.max(1, ...group.vaf_histogram);
        return (
          <g key={group.key} data-row={group.key}>
            <rect x={0} y={rowTop + row / 2 - 13} width={10} height={10} rx={2} fill={groupColor(group.key)} />
            <text className="nipt-row-label" x={16} y={rowTop + row / 2 - 4}>
              {groupLabel(group)}
            </text>
            <text x={16} y={rowTop + row / 2 + 10}>
              {formatCount(group.vaf_histogram.reduce((sum, count) => sum + count, 0))} sites · peak{' '}
              {formatCount(peak)}
            </text>
            <line className="nipt-row-base" x1={x(0)} x2={x(1)} y1={base} y2={base} />
            {hover && hover.group === group.key ? (
              <rect className="nipt-vaf-hover" x={x(hover.bin * binWidth)} width={step} y={rowTop} height={row} />
            ) : null}
            {group.vaf_histogram.map((count, bin) =>
              count ? (
                <rect
                  key={bin}
                  data-bin={bin}
                  data-group={group.key}
                  x={x(bin * binWidth) + 0.5}
                  width={Math.max(step - 1, 0.5)}
                  y={base - (count / peak) * (row - 4)}
                  height={(count / peak) * (row - 4)}
                  fill={groupColor(group.key)}
                />
              ) : null
            )}
            {group.expected_vaf != null ? (
              <g className="nipt-vaf-expected">
                <line x1={x(group.expected_vaf)} x2={x(group.expected_vaf)} y1={rowTop - 2} y2={base} />
              </g>
            ) : null}
            {group.observed_median_vaf != null ? (
              <path
                className="nipt-row-median"
                d={`M ${x(group.observed_median_vaf) - 4} ${rowTop - 3} h 8 l -4 6 z`}
              />
            ) : null}
            {group.vaf_histogram.map((_count, bin) => (
              <rect
                key={`hit-${bin}`}
                className="nipt-vaf-hit"
                x={x(bin * binWidth)}
                width={step}
                y={rowTop}
                height={row}
                onMouseMove={(event) => onHover({ group: group.key, bin, x: event.clientX, y: event.clientY })}
              />
            ))}
          </g>
        );
      })}
      <FractionAxis x={x} at={axisAt} height={height} />
    </svg>
  );
};

/** Every site, stacked by group: the plasma's allele fractions as they are, coloured by how the
 *  analysis classes them. A hom/hom peak far above the other bins is cut, so they can be read. */
const AllSites: React.FC<{
  stats: NiptVariantStatisticsOut;
  groups: NiptStatisticsGroupOut[];
  logScale: boolean;
  onHover: (hover: Hover | null) => void;
  hover: Hover | null;
}> = ({ stats, groups, logScale, onHover, hover }) => {
  const { width, height, left, right, top, bottom } = CHART;
  const binWidth = stats.vaf_bin_width;
  const bins = Math.max(0, ...groups.map((group) => group.vaf_histogram.length));
  const totals = Array.from({ length: bins }, (_, bin) =>
    groups.reduce((sum, group) => sum + (group.vaf_histogram[bin] ?? 0), 0)
  );
  const homHom = groups.find((group) => group.key === HOM_HOM);
  const isHomHomBin = (bin: number) => totals[bin] > 0 && (homHom?.vaf_histogram[bin] ?? 0) * 2 > totals[bin];
  const others = Math.max(0, ...totals.filter((_total, bin) => !isHomHomBin(bin)));
  const homPeak = Math.max(0, ...totals.filter((_total, bin) => isHomHomBin(bin)));
  const cut = !logScale && others > 0 && homPeak > CUT_RATIO * others;
  const x = d3.scaleLinear().domain([0, 1]).range([left, width - right]);
  const y = logScale
    ? d3.scaleSymlog().constant(1).domain([0, Math.max(1, ...totals)]).range([height - bottom, top])
    : d3
        .scaleLinear()
        .domain([0, cut ? others * 1.2 : Math.max(1, ...totals)])
        // Rounded to the five ticks drawn, so the top of the axis is a tick.
        .nice(5)
        .range([height - bottom, top]);
  const ceiling = y.domain()[1];
  const yTicks = logScale
    ? [0, 1, 10, 100, 1000, 10000, 100000].filter((tick) => tick <= ceiling)
    : y.ticks(5).filter((tick) => Number.isInteger(tick));
  const step = x(binWidth) - x(0);
  const barWidth = Math.max(step - 1, 0.5);
  const marks = expectedMarks(stats.groups);
  const cutBins = totals.map((total, bin) => (total > ceiling ? bin : -1)).filter((bin) => bin >= 0);
  const tallest = cutBins.reduce((best, bin) => (totals[bin] > totals[best] ? bin : best), cutBins[0] ?? -1);
  const sites = totals.reduce((sum, count) => sum + count, 0);
  return (
    <>
      <svg
        className="nipt-vaf-chart"
        viewBox={`0 0 ${width} ${height}`}
        role="img"
        aria-label={`Plasma allele fractions of ${formatCount(sites)} sites in bins of ${binWidth}, stacked by group, with the expected fraction of each category at the fetal fraction`}
        onMouseLeave={() => onHover(null)}
      >
        {yTicks.map((tick) => (
          <g key={`y-${tick}`}>
            <line className="nipt-vaf-grid" x1={left} x2={width - right} y1={y(tick)} y2={y(tick)} />
            <text x={left - 8} y={y(tick) + 4} textAnchor="end">
              {formatCount(tick)}
            </text>
          </g>
        ))}
        {hover && hover.group == null ? (
          <rect className="nipt-vaf-hover" x={x(hover.bin * binWidth)} width={step} y={top} height={height - bottom - top} />
        ) : null}
        {totals.map((_total, bin) => {
          let base = 0;
          return groups.map((group) => {
            const count = group.vaf_histogram[bin] ?? 0;
            if (!count) return null;
            const lower = base;
            base += count;
            if (lower >= ceiling) return null;
            const upper = Math.min(base, ceiling);
            return (
              <rect
                key={`${group.key}-${bin}`}
                data-bin={bin}
                data-group={group.key}
                x={x(bin * binWidth) + 0.5}
                width={barWidth}
                y={y(upper)}
                height={Math.max(y(lower) - y(upper), 0)}
                fill={groupColor(group.key)}
              />
            );
          });
        })}
        {cutBins.map((bin) => (
          <path
            key={`cut-${bin}`}
            className="nipt-vaf-cut"
            d={`M ${x(bin * binWidth)} ${y(ceiling) + 9} l ${step} -4 M ${x(bin * binWidth)} ${y(ceiling) + 14} l ${step} -4`}
          />
        ))}
        {tallest >= 0 ? (
          <text className="nipt-vaf-cut-label" x={x(tallest * binWidth) - 4} y={y(ceiling) + 12} textAnchor="end">
            {formatCount(totals[tallest])} ↑
          </text>
        ) : null}
        {marks.map((mark) => (
          <g key={`expected-${mark.at}`} className="nipt-vaf-expected">
            <line x1={x(mark.at)} x2={x(mark.at)} y1={top - 4} y2={height - bottom} />
            <text className="nipt-vaf-expected-label" x={x(mark.at)} y={top - 10} textAnchor="middle">
              {mark.categories.join('/')}
            </text>
          </g>
        ))}
        <FractionAxis x={x} at={height - bottom} height={height} />
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
            width={step}
            y={top}
            height={height - bottom - top}
            onMouseMove={(event) => onHover({ bin, x: event.clientX, y: event.clientY })}
          />
        ))}
      </svg>
      {tallest >= 0 ? (
        <p className="analysis-count" role="note">
          The hom/hom peak is cut at {formatCount(ceiling)} sites so that the other bins can be read: its
          tallest bin, {binRange(tallest, binWidth)}, holds {formatCount(totals[tallest])}. The log scale
          shows it whole.
        </p>
      ) : null}
    </>
  );
};

/** The plasma's allele fractions, per group (each row on its own scale) or all sites stacked by
 *  group, with the fraction each category expects at the family's fetal fraction; hovering a bin
 *  lists its sites. */
export const AlleleFractionPlot: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const [view, setView] = React.useState<PlotView>('groups');
  const [logScale, setLogScale] = React.useState(false);
  const [hover, setHover] = React.useState<Hover | null>(null);
  const groups = drawnGroups(stats);
  const binWidth = stats.vaf_bin_width;
  const hovered = hover ? groups.filter((group) => (hover.group ?? group.key) === group.key) : [];
  const inBin = hover ? hovered.filter((group) => group.vaf_histogram[hover.bin]) : [];
  const binTotal = hover ? inBin.reduce((sum, group) => sum + group.vaf_histogram[hover.bin], 0) : 0;
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <span className="stat-label">Show</span>
        {PLOT_VIEWS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            className={`variant-quick-toggle${view === key ? ' variant-quick-toggle--active' : ''}`}
            aria-pressed={view === key}
            onClick={() => {
              setView(key);
              setHover(null);
            }}
          >
            {label}
          </button>
        ))}
        {view === 'all' ? (
          <button type="button" onClick={() => setLogScale((value) => !value)} className="button-ghost">
            {logScale ? 'Linear scale' : 'Log scale'}
          </button>
        ) : null}
      </div>
      {view === 'all' ? (
        <div className="nipt-legend">
          {groups.map((group) => (
            <span key={group.key} className="nipt-legend-item">
              <Swatch groupKey={group.key} />
              {groupLabel(group)}
            </span>
          ))}
        </div>
      ) : null}
      {groups.length ? (
        <div className="viz-panel nipt-vaf-scroll">
          {view === 'groups' ? (
            <GroupRows stats={stats} groups={groups} hover={hover} onHover={setHover} />
          ) : (
            <AllSites stats={stats} groups={groups} logScale={logScale} hover={hover} onHover={setHover} />
          )}
        </div>
      ) : (
        <p className="analysis-count">No group has a plasma call to draw.</p>
      )}
      {hover && binTotal ? (
        <VizTooltip x={hover.x} y={hover.y}>
          <p className="font-semibold">
            {binRange(hover.bin, binWidth)} · {formatCount(binTotal)} sites
          </p>
          {inBin.map((group) => (
            <p key={group.key} className="nipt-tip-row">
              <span className="nipt-group-name">
                <Swatch groupKey={group.key} />
                {groupLabel(group)}
              </span>
              <span>{formatCount(group.vaf_histogram[hover.bin])}</span>
            </p>
          ))}
          {hover.group != null && inBin[0] ? (
            <p className="table-subtle">
              Expected {vaf(inBin[0].expected_vaf)} · observed median {vaf(inBin[0].observed_median_vaf)}
            </p>
          ) : null}
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
          ['Removed by the artefact list, after the quality filter', formatCount(filters.failed_artifact ?? 0)],
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

const ARTIFACT_PROTECTION_LABEL: Record<string, string> = {
  clinvar: 'ClinVar P/LP',
  common: 'common',
};

/** What the assay's recurrent-artifact list did to the analysis: an empty list says so. */
const ArtifactCard: React.FC<{ artifacts: NiptVariantStatisticsOut['artifacts'] }> = ({ artifacts }) => {
  const kept = Object.entries(artifacts.protected ?? {}).filter(([, count]) => count > 0);
  const keptCount = kept.reduce((sum, [, count]) => sum + count, 0);
  return (
    <div className="stat-card">
      <span className="stat-label">Artefact list</span>
      <span className="stat-value">
        {artifacts.listed ? `${formatCount(artifacts.removed)} removed` : 'Empty'}
      </span>
      <span className="table-subtle">
        {artifacts.listed
          ? `${formatCount(artifacts.in_family)} of the ${formatCount(artifacts.listed)} alleles the assay lists are in this family` +
            (keptCount
              ? `; ${formatCount(keptCount)} kept in (${kept
                  .map(([reason, count]) => `${ARTIFACT_PROTECTION_LABEL[reason] ?? reason} ${formatCount(count)}`)
                  .join(', ')})`
              : '')
          : 'The assay’s list holds no allele: nothing was removed.'}
      </span>
    </div>
  );
};

const Facts: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const ff = stats.fetal_fraction;
  const { paternity, fetal_sex: sex } = stats.qc;
  return (
    <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
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
      <ArtifactCard artifacts={stats.artifacts} />
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
            The sites by their plasma allele fraction, in bins of {stats.vaf_bin_width}.{' '}
            <em>Per group</em> gives each group a row on its own scale, so a small group reads as
            well as a large one; the dashed line is where its category is expected at this fetal
            fraction and the triangle its observed median. <em>All sites</em> stacks the groups, as
            the plasma’s calls are, with numbered lines where the categories are expected; a hom/hom
            peak far above the rest is cut so that the other bins can be read. Peaks off their lines
            mean the fetal fraction or the classes do not fit the data. Hover a bin for its sites.
          </p>
          <AlleleFractionPlot stats={stats} />

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
