import React from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import Histogram from '../../components/visualizations/Histogram';
import type {
  FamilySmallVariantStatisticsOut,
  HistogramBinOut,
  MendelianTrioOut,
  SmallVariantSampleStatisticsOut,
} from '../../lib/apiSchema.generated';
import { CONSEQUENCE_GROUPS } from './smallVariantFilterSections';
import { VariantSummarySectionState } from './VariantSummarySectionState';

/**
 * The variant summary's small variants (PROD-1): the family's nuclear SNVs and indels from its
 * directly called callsets, as `GET /families/{id}/small-variant-statistics` counts them. The
 * server counts; this lays the figures out per sample.
 */

const formatCount = (value: number): string => value.toLocaleString('en-US');

/** A ratio to two decimals, or a dash when its denominator is 0. */
export const formatRatio = (numerator: number, denominator: number): string =>
  denominator > 0 ? (numerator / denominator).toFixed(2) : '–';

const formatPercent = (part: number, whole: number): string =>
  whole > 0 ? `${((100 * part) / whole).toFixed(1)} %` : '–';

// The terms in the coding sequence (Sequence Ontology's coding_sequence_variant and its
// children), for the consequence table's first line.
const CODING_TERMS = new Set([
  'stop_gained',
  'frameshift_variant',
  'stop_lost',
  'start_lost',
  'inframe_insertion',
  'inframe_deletion',
  'missense_variant',
  'protein_altering_variant',
  'incomplete_terminal_codon_variant',
  'start_retained_variant',
  'stop_retained_variant',
  'synonymous_variant',
  'coding_sequence_variant',
]);

interface ConsequenceRow {
  term: string;
  label: string;
  counts: number[];
}

interface ConsequenceGroupRows {
  id: string;
  label: string;
  rows: ConsequenceRow[];
  totals: number[];
}

/**
 * The samples' consequence counts in the Annotations filter's groups and order: a group, its
 * terms that some sample has, and its total; a term no group names goes under *Other*, the
 * server's `other` (a term outside VEP's order) and `none` (no term) last.
 */
export const groupConsequences = (
  samples: SmallVariantSampleStatisticsOut[]
): ConsequenceGroupRows[] => {
  const countsByTerm = new Map<string, number[]>();
  samples.forEach((sample, index) => {
    sample.consequences.forEach(({ consequence, count }) => {
      const counts = countsByTerm.get(consequence) ?? samples.map(() => 0);
      counts[index] += count;
      countsByTerm.set(consequence, counts);
    });
  });
  const placed = new Set<string>();
  const groups: ConsequenceGroupRows[] = CONSEQUENCE_GROUPS.map((group) => {
    const rows = group.options
      .map((option) => ({
        term: option.value.toLowerCase(),
        label: option.label,
      }))
      .filter(({ term }) => countsByTerm.has(term))
      .map(({ term, label }) => {
        placed.add(term);
        return { term, label, counts: countsByTerm.get(term) as number[] };
      });
    return { id: group.id, label: group.label, rows, totals: [] };
  });
  const unplaced = [...countsByTerm.keys()].filter(
    (term) => !placed.has(term) && term !== 'other' && term !== 'none'
  );
  const otherGroup = groups.find((group) => group.id === 'other');
  unplaced.forEach((term) => {
    const row = {
      term,
      label: term.replace(/_/g, ' '),
      counts: countsByTerm.get(term) as number[],
    };
    if (otherGroup) otherGroup.rows.push(row);
    else groups.push({ id: term, label: term, rows: [row], totals: [] });
  });
  const tail: ConsequenceRow[] = [];
  if (countsByTerm.has('other')) {
    tail.push({
      term: 'other',
      label: 'A term outside VEP’s list',
      counts: countsByTerm.get('other') as number[],
    });
  }
  if (countsByTerm.has('none')) {
    tail.push({
      term: 'none',
      label: 'No consequence annotated',
      counts: countsByTerm.get('none') as number[],
    });
  }
  if (tail.length)
    groups.push({
      id: 'unannotated',
      label: 'Not classified',
      rows: tail,
      totals: [],
    });
  return groups
    .filter((group) => group.rows.length)
    .map((group) => ({
      ...group,
      totals: samples.map((_, index) =>
        group.rows.reduce((sum, row) => sum + row.counts[index], 0)
      ),
    }));
};

const codingCounts = (samples: SmallVariantSampleStatisticsOut[]): number[] =>
  samples.map((sample) =>
    sample.consequences.reduce(
      (sum, row) => sum + (CODING_TERMS.has(row.consequence) ? row.count : 0),
      0
    )
  );

type QualityMetric = 'genotype_quality' | 'depth' | 'allele_balance';

const QUALITY_METRICS: {
  key: QualityMetric;
  label: string;
  subject: string;
}[] = [
  {
    key: 'genotype_quality',
    label: 'Genotype quality',
    subject: 'genotype qualities',
  },
  { key: 'depth', label: 'Depth', subject: 'read depths' },
  {
    key: 'allele_balance',
    label: 'Allele balance (het calls)',
    subject: 'allele balances of heterozygous calls',
  },
];

const histogramOf = (
  sample: SmallVariantSampleStatisticsOut,
  metric: QualityMetric
): HistogramBinOut[] =>
  metric === 'genotype_quality'
    ? sample.genotype_quality_histogram
    : metric === 'depth'
      ? sample.depth_histogram
      : sample.allele_balance_histogram;

const missingOf = (
  sample: SmallVariantSampleStatisticsOut,
  metric: QualityMetric
): number =>
  metric === 'genotype_quality'
    ? sample.genotype_quality_missing
    : metric === 'depth'
      ? sample.depth_missing
      : sample.allele_balance_missing;

/** A bin's label: its lower edge, with a `+` on the last, open-ended bin. */
export const binLabels = (
  bins: HistogramBinOut[],
  metric: QualityMetric
): string[] =>
  bins.map((bin, index) => {
    const edge =
      metric === 'allele_balance' ? bin.low.toFixed(2) : String(bin.low);
    return index === bins.length - 1 ? `${edge}+` : edge;
  });

// A monogenic NIPT family's plasma and father: their GT is not their genotype, so their genotype
// counts are not shown (the NIPT-M tab classes their sites by allele fraction).
const NOT_A_GENOTYPE = '—';

const PerSampleTable: React.FC<{
  samples: SmallVariantSampleStatisticsOut[];
  niptSamples: ReadonlySet<string>;
}> = ({ samples, niptSamples }) => {
  const genotype = (s: SmallVariantSampleStatisticsOut, value: string) =>
    niptSamples.has(s.sample_id) ? NOT_A_GENOTYPE : value;
  const rows: { label: string; values: string[] }[] = [
    {
      label: 'Variants',
      values: samples.map((s) => formatCount(s.non_ref_count)),
    },
    {
      label: 'Heterozygous',
      values: samples.map((s) => genotype(s, formatCount(s.het_count))),
    },
    {
      label: 'Homozygous alt',
      values: samples.map((s) => genotype(s, formatCount(s.hom_alt_count))),
    },
    {
      label: 'Het / hom ratio',
      values: samples.map((s) =>
        genotype(s, formatRatio(s.het_count, s.hom_alt_count))
      ),
    },
    { label: 'SNVs', values: samples.map((s) => formatCount(s.snv_count)) },
    {
      label: 'Insertions',
      values: samples.map((s) => formatCount(s.insertion_count)),
    },
    {
      label: 'Deletions',
      values: samples.map((s) => formatCount(s.deletion_count)),
    },
    {
      label: 'Other (MNV, complex)',
      values: samples.map((s) => formatCount(s.other_count)),
    },
    {
      label: 'Transitions / transversions',
      values: samples.map((s) =>
        formatRatio(s.transition_count, s.transversion_count)
      ),
    },
    {
      label: 'Passed FILTER',
      values: samples.map((s) =>
        formatPercent(s.passing_count, s.non_ref_count)
      ),
    },
  ];
  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky">
        <thead>
          <tr>
            <th>Per sample</th>
            {samples.map((sample) => (
              <th key={sample.sample_id} className="text-center">
                {sample.sample_id}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.label}>
              <td>{row.label}</td>
              {row.values.map((value, index) => (
                <td key={samples[index].sample_id} className="text-center">
                  {value}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const ConsequenceTable: React.FC<{
  samples: SmallVariantSampleStatisticsOut[];
}> = ({ samples }) => {
  const groups = groupConsequences(samples);
  const coding = codingCounts(samples);
  if (!groups.length)
    return <p className="analysis-count">No consequences are annotated.</p>;
  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky">
        <thead>
          <tr>
            <th>Most severe consequence</th>
            {samples.map((sample) => (
              <th key={sample.sample_id} className="text-center">
                {sample.sample_id}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          <tr className="variant-summary-group-row">
            <td>In the coding sequence</td>
            {coding.map((count, index) => (
              <td key={samples[index].sample_id} className="text-center">
                {formatCount(count)}
              </td>
            ))}
          </tr>
          {groups.map((group) => (
            <React.Fragment key={group.id}>
              <tr className="variant-summary-group-row">
                <td>{group.label}</td>
                {group.totals.map((count, index) => (
                  <td key={samples[index].sample_id} className="text-center">
                    {formatCount(count)}
                  </td>
                ))}
              </tr>
              {group.rows.map((row) => (
                <tr key={row.term}>
                  <td className="variant-summary-term">{row.label}</td>
                  {row.counts.map((count, index) => (
                    <td key={samples[index].sample_id} className="text-center">
                      {formatCount(count)}
                    </td>
                  ))}
                </tr>
              ))}
            </React.Fragment>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const ChromosomeTable: React.FC<{
  samples: SmallVariantSampleStatisticsOut[];
}> = ({ samples }) => {
  const order: string[] = [];
  const counts = new Map<string, Map<string, { het: number; hom: number }>>();
  samples.forEach((sample) =>
    sample.chromosomes.forEach((row) => {
      if (!counts.has(row.chrom)) {
        order.push(row.chrom);
        counts.set(row.chrom, new Map());
      }
      counts
        .get(row.chrom)
        ?.set(sample.sample_id, { het: row.het_count, hom: row.hom_alt_count });
    })
  );
  // The server orders each sample's chromosomes; a chromosome another sample lacks keeps
  // its place in the karyotype by the first sample that has it.
  const rank = (chrom: string) => {
    const number = Number(chrom);
    if (Number.isInteger(number) && number > 0) return number;
    return chrom === 'X' ? 23 : chrom === 'Y' ? 24 : 25;
  };
  const chromosomes = [...order].sort(
    (a, b) => rank(a) - rank(b) || a.localeCompare(b)
  );
  return (
    <div className="analysis-results-card overflow-x-auto">
      <table className="analysis-table table-sticky">
        <thead>
          <tr>
            <th rowSpan={2}>Chromosome</th>
            {samples.map((sample) => (
              <th key={sample.sample_id} colSpan={3} className="text-center">
                {sample.sample_id}
              </th>
            ))}
          </tr>
          <tr>
            {samples.map((sample) => (
              <React.Fragment key={sample.sample_id}>
                <th className="text-center">Het</th>
                <th className="text-center">Hom</th>
                <th className="text-center">Hom %</th>
              </React.Fragment>
            ))}
          </tr>
        </thead>
        <tbody>
          {chromosomes.map((chrom) => (
            <tr key={chrom}>
              <td>{chrom}</td>
              {samples.map((sample) => {
                const cell = counts.get(chrom)?.get(sample.sample_id) ?? {
                  het: 0,
                  hom: 0,
                };
                return (
                  <React.Fragment key={sample.sample_id}>
                    <td className="text-center">{formatCount(cell.het)}</td>
                    <td className="text-center">{formatCount(cell.hom)}</td>
                    <td className="text-center">
                      {formatPercent(cell.hom, cell.het + cell.hom)}
                    </td>
                  </React.Fragment>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
};

const CallQuality: React.FC<{ samples: SmallVariantSampleStatisticsOut[] }> = ({
  samples,
}) => {
  const [metric, setMetric] = React.useState<QualityMetric>('genotype_quality');
  const [logScale, setLogScale] = React.useState(false);
  const shown =
    QUALITY_METRICS.find((entry) => entry.key === metric) ?? QUALITY_METRICS[0];
  return (
    <div className="space-y-2">
      <div className="analysis-graph-toolbar">
        <span className="stat-label">Show</span>
        {QUALITY_METRICS.map((entry) => (
          <button
            key={entry.key}
            type="button"
            className={`variant-quick-toggle${entry.key === metric ? ' variant-quick-toggle--active' : ''}`}
            aria-pressed={entry.key === metric}
            onClick={() => setMetric(entry.key)}
          >
            {entry.label}
          </button>
        ))}
        <button
          type="button"
          onClick={() => setLogScale((value) => !value)}
          className="button-ghost"
        >
          {logScale ? 'Linear scale' : 'Log scale'}
        </button>
      </div>
      <div className="variant-summary-grid">
        {samples.map((sample) => {
          const bins = histogramOf(sample, metric);
          const missing = missingOf(sample, metric);
          return (
            <div key={sample.sample_id} className="viz-panel space-y-1">
              <h4 className="font-semibold">{sample.sample_id}</h4>
              <Histogram
                data={[]}
                counts={bins.map((bin) => bin.count)}
                binLabels={binLabels(bins, metric)}
                subject={`${sample.sample_id}'s ${shown.subject}`}
                logScale={logScale}
              />
              {missing ? (
                <p className="analysis-count">
                  {formatCount(missing)} calls without a value are not drawn.
                </p>
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
};

const MendelianTable: React.FC<{ trio: MendelianTrioOut }> = ({ trio }) => {
  const totals = trio.chromosomes.reduce(
    (sum, row) => ({
      sites: sum.sites + row.sites,
      father: sum.father + row.not_from_father,
      mother: sum.mother + row.not_from_mother,
      neither: sum.neither + row.from_neither,
    }),
    { sites: 0, father: 0, mother: 0, neither: 0 }
  );
  const cells = (
    sites: number,
    father: number,
    mother: number,
    neither: number
  ) => [
    formatCount(sites),
    formatCount(father),
    formatCount(mother),
    formatCount(neither),
    formatPercent(father + mother + neither, sites),
  ];
  return (
    <div className="viz-panel space-y-2">
      <h4 className="font-semibold">
        {trio.child}, child of {trio.father} and {trio.mother} ({trio.source})
      </h4>
      <div className="analysis-results-card overflow-x-auto">
        <table className="analysis-table table-sticky">
          <thead>
            <tr>
              <th>Chromosome</th>
              <th className="text-center">Sites</th>
              <th className="text-center">Father’s allele missing</th>
              <th className="text-center">Mother’s allele missing</th>
              <th className="text-center">From neither parent</th>
              <th className="text-center">Inconsistent</th>
            </tr>
          </thead>
          <tbody>
            {trio.chromosomes.map((row) => (
              <tr key={row.chrom}>
                <td>{row.chrom}</td>
                {cells(
                  row.sites,
                  row.not_from_father,
                  row.not_from_mother,
                  row.from_neither
                ).map((value, index) => (
                  <td key={index} className="text-center">
                    {value}
                  </td>
                ))}
              </tr>
            ))}
            <tr className="variant-summary-group-row">
              <td>Autosomes</td>
              {cells(
                totals.sites,
                totals.father,
                totals.mother,
                totals.neither
              ).map((value, index) => (
                <td key={index} className="text-center">
                  {value}
                </td>
              ))}
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
};

const describeTotals = (stats: FamilySmallVariantStatisticsOut): string =>
  `${formatCount(stats.total_variants)} variants in the family: ${formatCount(stats.snv_count)} SNVs, ` +
  `${formatCount(stats.insertion_count)} insertions, ${formatCount(stats.deletion_count)} deletions and ` +
  `${formatCount(stats.other_count)} other (MNV or complex).`;

const VariantSummarySmallVariants: React.FC<{
  familyId: string;
  projectId?: string | null;
}> = ({ familyId, projectId }) => {
  const query = useQuery<FamilySmallVariantStatisticsOut>({
    queryKey: [
      'family',
      familyId,
      'small-variant-statistics',
      projectId || null,
    ],
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/small-variant-statistics`,
        {
          params: projectId ? { project_id: projectId } : undefined,
        }
      );
      return res.data as FamilySmallVariantStatisticsOut;
    },
  });
  const stats = query.data;
  const niptSamples = new Set(stats?.nipt_samples ?? []);
  // The per-chromosome table counts genotypes: a NIPT family's plasma and father are left out.
  const genotypedSamples = (stats?.samples ?? []).filter(
    (sample) => !niptSamples.has(sample.sample_id)
  );
  return (
    <section id="small-variants" className="analysis-panel space-y-4">
      <h2 className="section-title">Small variants (SNV/indel)</h2>
      {!stats ? (
        <VariantSummarySectionState
          what="small-variant statistics"
          loading={query.isLoading}
          error={query.error}
          onRetry={() => void query.refetch()}
        />
      ) : !stats.samples.length ? (
        <p className="analysis-count">
          {stats.imputed_sources.length
            ? `This family has imputed genotypes only (${stats.imputed_sources.join(', ')}); they are not summarised here.`
            : 'This family has no small variants outside chrM.'}
        </p>
      ) : (
        <>
          <p className="analysis-count">
            {describeTotals(stats)} Counted from {stats.sources.join(', ')}
            {stats.imputed_sources.length
              ? `; imputed genotypes (${stats.imputed_sources.join(', ')}) are not`
              : ''}
            . chrM is summarised under mtDNA. A variant two callsets hold counts
            once.
          </p>

          {niptSamples.size ? <NiptSamplesNote names={[...niptSamples]} /> : null}

          <h3 className="font-semibold">Per sample</h3>
          <PerSampleTable samples={stats.samples} niptSamples={niptSamples} />

          <h3 className="font-semibold">Consequences</h3>
          <p className="analysis-count">
            Each variant once, under its most severe consequence over all its
            transcripts (VEP’s order), grouped as the small-variant filter
            groups them. The small-variant search may name another transcript.
          </p>
          <ConsequenceTable samples={stats.samples} />

          <h3 className="font-semibold">Per chromosome</h3>
          <PerChromosomeNote />
          {genotypedSamples.length ? (
            <ChromosomeTable samples={genotypedSamples} />
          ) : (
            <p className="analysis-count">
              No sample here has genotypes of its own to count: the NIPT-M tab
              classes the plasma’s and the father’s sites.
            </p>
          )}

          <h3 className="font-semibold">Call quality</h3>
          <p className="analysis-count">
            One value per call: a variant two callsets hold is drawn twice.
            Allele balance is the fraction of reads carrying the ALT allele, for
            heterozygous calls.
          </p>
          <CallQuality samples={stats.samples} />

          <h3 className="font-semibold">Mendelian inconsistencies</h3>
          <p className="analysis-count">
            Per child with both parents in the family, per callset and autosome:
            the sites where all three have a biallelic diploid call and one
            carries the ALT allele, and those no transmission explains. A
            chromosome where most inconsistencies miss one parent’s allele can
            point to uniparental disomy or a deletion in the child. These counts
            describe the calls; CoGA does not call UPD.
          </p>
          {stats.trios.length ? (
            stats.trios.map((trio) => (
              <MendelianTable
                key={`${trio.child}-${trio.source}`}
                trio={trio}
              />
            ))
          ) : (
            <p className="analysis-count">
              No child has its own and both parents’ calls in one callset.
            </p>
          )}
        </>
      )}
    </section>
  );
};

const NiptSamplesNote: React.FC<{ names: string[] }> = ({ names }) => (
  <p className="analysis-count" role="note">
    {names.join(' and ')} {names.length > 1 ? 'are' : 'is'} this monogenic NIPT
    family’s plasma and father, whose GT is not a genotype: the plasma holds the
    mother’s and the fetus’s DNA, and Mutect2 in tumour-only mode, the pair’s
    caller, writes 0/1 on every call. Their heterozygous and homozygous counts
    are left out here and in the per-chromosome table, and their allele balance
    covers every call; the NIPT-M tab classes their sites by allele fraction.
    Their FILTER is the caller’s own: Mutect2’s somatic filters flag many
    germline calls, and the NIPT analysis does not use them as a gate.
  </p>
);

const PerChromosomeNote: React.FC = () => (
  <p className="analysis-count">
    A sample’s variants per chromosome, heterozygous and homozygous. A
    chromosome with an unusually high share of homozygous calls can point to a
    long run of homozygosity.
  </p>
);

export default VariantSummarySmallVariants;
