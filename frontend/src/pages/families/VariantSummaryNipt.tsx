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

const GroupEntry: React.FC<{
  group: NiptStatisticsGroupOut;
  total: number;
  split: Split;
}> = ({ group, total, split }) => {
  const detail = splitText(group, split);
  return (
    <div className="nipt-matrix-group">
      <p className="nipt-matrix-group-title">
        <span
          className="nipt-swatch"
          style={{ background: groupColor(group.key) }}
          aria-hidden="true"
        />
        {groupLabel(group)}
      </p>
      <p className="nipt-matrix-count">
        {formatCount(group.sites)}{' '}
        <span className="table-subtle">{share(group.sites, total)}</span>
      </p>
      <p className="table-subtle">
        {group.expected_vaf != null
          ? `Expected ${vaf(group.expected_vaf)} · observed ${vaf(group.observed_median_vaf)}`
          : group.observed_median_vaf != null
            ? `Allele absent · observed ${vaf(group.observed_median_vaf)}`
            : 'Allele absent'}
        {group.sites
          ? ` · ${share(group.high_confidence, group.sites)} at confidence ≥ 0.99`
          : ''}
      </p>
      {detail ? <p className="table-subtle">{detail}</p> : null}
    </div>
  );
};

export const GenotypeMatrix: React.FC<{ stats: NiptVariantStatisticsOut }> = ({ stats }) => {
  const [split, setSplit] = React.useState<Split>('none');
  const total = stats.classified_sites;
  const cell = (mother: Genotype, fetus: Genotype) =>
    stats.groups.filter(
      (group) => group.maternal_genotype === mother && group.fetal_genotype === fetus
    );
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <span className="stat-label">Split each group by</span>
        {(
          [
            ['none', 'Nothing'],
            ['father', "Father's genotype"],
            ['variant_class', 'Variant class'],
          ] as const
        ).map(([key, label]) => (
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
        <table className="analysis-table nipt-matrix" aria-label="Plasma sites by the mother's and the fetus's genotype">
          <thead>
            <tr>
              <th scope="col">Mother ↓ · fetus →</th>
              {GENOTYPES.map((fetus) => (
                <th key={fetus} scope="col">
                  Fetus {GENOTYPE_LABEL[fetus].toLowerCase()}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {GENOTYPES.map((mother) => (
              <tr key={mother}>
                <th scope="row">Mother {GENOTYPE_LABEL[mother].toLowerCase()}</th>
                {GENOTYPES.map((fetus) => {
                  const groups = cell(mother, fetus);
                  return groups.length ? (
                    <td key={fetus}>
                      {groups.map((group) => (
                        <GroupEntry key={group.key} group={group} total={total} split={split} />
                      ))}
                    </td>
                  ) : (
                    <td key={fetus} className="nipt-matrix-empty">
                      Not modelled
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
};

const HISTOGRAM = { width: 720, height: 240, left: 48, right: 12, top: 12, bottom: 36 };

/** The plasma allele fractions, stacked by group, with the fraction each category predicts at
 *  the family's fetal fraction drawn as a dashed line. */
export const AlleleFractionHistogram: React.FC<{ stats: NiptVariantStatisticsOut }> = ({
  stats,
}) => {
  const [logScale, setLogScale] = React.useState(false);
  const groups = stats.groups.filter((group) => group.vaf_histogram.some((count) => count > 0));
  const bins = Math.max(0, ...stats.groups.map((group) => group.vaf_histogram.length));
  const totals = Array.from({ length: bins }, (_, bin) =>
    groups.reduce((sum, group) => sum + (group.vaf_histogram[bin] ?? 0), 0)
  );
  const scale = (count: number) => (logScale ? Math.log10(1 + count) : count);
  const top = Math.max(1, ...totals.map(scale));
  const { width, height, left, right, top: padTop, bottom } = HISTOGRAM;
  const plotWidth = width - left - right;
  const plotHeight = height - padTop - bottom;
  const x = (fraction: number) => left + fraction * plotWidth;
  const y = (value: number) => padTop + plotHeight - (value / top) * plotHeight;
  const binWidth = bins ? plotWidth / bins : 0;
  const expected = stats.groups.filter((group) => group.expected_vaf != null && group.sites > 0);
  const ticks = [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1];
  const yTicks = logScale
    ? [1, 10, 100, 1000, 10000].filter((tick) => Math.log10(1 + tick) <= top)
    : d3.scaleLinear().domain([0, top]).ticks(4).filter((tick) => Number.isInteger(tick));
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <div className="nipt-legend">
          {groups.map((group) => (
            <span key={group.key} className="nipt-legend-item">
              <span className="nipt-swatch" style={{ background: groupColor(group.key) }} aria-hidden="true" />
              {groupLabel(group)}
            </span>
          ))}
        </div>
        <button type="button" onClick={() => setLogScale((value) => !value)} className="button-ghost">
          {logScale ? 'Linear scale' : 'Log scale'}
        </button>
      </div>
      <div className="viz-panel">
        <svg
          viewBox={`0 0 ${width} ${height}`}
          width="100%"
          role="img"
          aria-label={`Plasma allele fractions of ${formatCount(totals.reduce((a, b) => a + b, 0))} sites, stacked by group, with the expected fraction of each category at the fetal fraction`}
        >
          {yTicks.map((tick) => (
            <g key={`y-${tick}`}>
              <line x1={left} x2={width - right} y1={y(scale(tick))} y2={y(scale(tick))} stroke="var(--color-grid)" />
              <text x={left - 6} y={y(scale(tick)) + 3} textAnchor="end" fontSize="10" fill="var(--color-text-muted)">
                {formatCount(tick)}
              </text>
            </g>
          ))}
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
                  x={left + bin * binWidth + 0.5}
                  width={Math.max(binWidth - 1, 0.5)}
                  y={y(scale(base))}
                  height={Math.max(y(scale(lower)) - y(scale(base)), 0)}
                  fill={groupColor(group.key)}
                >
                  <title>{`${groupLabel(group)}: ${formatCount(count)} sites at ${(bin * stats.vaf_bin_width).toFixed(2)}–${((bin + 1) * stats.vaf_bin_width).toFixed(2)}`}</title>
                </rect>
              );
            });
          })}
          {expected.map((group) => (
            <line
              key={`expected-${group.key}`}
              x1={x(group.expected_vaf ?? 0)}
              x2={x(group.expected_vaf ?? 0)}
              y1={padTop}
              y2={padTop + plotHeight}
              stroke="var(--color-text-muted)"
              strokeDasharray="4 3"
            >
              <title>{`${groupLabel(group)}: expected ${vaf(group.expected_vaf)}`}</title>
            </line>
          ))}
          <line x1={left} x2={width - right} y1={padTop + plotHeight} y2={padTop + plotHeight} stroke="var(--color-axis)" />
          {ticks.map((tick) => (
            <text key={`x-${tick}`} x={x(tick)} y={padTop + plotHeight + 14} textAnchor="middle" fontSize="10" fill="var(--color-text-muted)">
              {tick.toFixed(1)}
            </text>
          ))}
          <text x={left + plotWidth / 2} y={height - 4} textAnchor="middle" fontSize="11" fill="var(--color-text-muted)">
            Plasma allele fraction
          </text>
        </svg>
      </div>
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
            <th className="text-center">Father’s het alleles inherited</th>
            <th className="text-center">Father’s hom-alt alleles inherited</th>
            <th className="text-center">Mother’s het alleles inherited</th>
            <th className="text-center">Fetus hom alt</th>
            <th>Marked</th>
          </tr>
        </thead>
        <tbody>
          {stats.chromosomes.map((row) => (
            <tr key={row.chrom} className={row.marks.length ? 'nipt-transmission-marked' : undefined}>
              <td>{row.chrom}</td>
              <td className="text-center">
                {ratio(row.paternal_het_transmitted, row.paternal_het_transmitted + row.paternal_het_not_transmitted)}
              </td>
              <td className="text-center">
                {ratio(row.paternal_hom_alt_transmitted, row.paternal_hom_alt_transmitted + row.paternal_hom_alt_not_transmitted)}
              </td>
              <td className="text-center">
                {ratio(row.maternal_het_inherited, row.maternal_het_inherited + row.maternal_het_not_inherited)}
              </td>
              <td className="text-center">{formatCount(row.fetal_hom_alt)}</td>
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
            <td className="text-center">{ratio(totals.paternalHet, totals.paternalHetAll)}</td>
            <td className="text-center">{ratio(totals.paternalHom, totals.paternalHomAll)}</td>
            <td className="text-center">{ratio(totals.maternal, totals.maternalAll)}</td>
            <td className="text-center">{formatCount(totals.fetalHomAlt)}</td>
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
            <td className="text-right">{value}</td>
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
            Each group with its share of the grouped sites, the plasma allele fraction its
            category predicts at this fetal fraction beside the median observed, and the share
            classified with a confidence of 0.99 or more. A low share in the mother’s het row means
            the fetal fraction separates those groups poorly.
          </p>
          <GenotypeMatrix stats={stats} />

          <h3 className="font-semibold">Plasma allele fraction</h3>
          <p className="analysis-count">
            Each group’s sites by their plasma allele fraction; the dashed lines are where the
            categories are expected at this fetal fraction. Peaks off their lines mean the fetal
            fraction or the classes do not fit the data.
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
