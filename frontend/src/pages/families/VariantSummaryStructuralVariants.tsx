import React from 'react';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import { apiPath } from '../../lib/apiPath';
import Histogram from '../../components/visualizations/Histogram';
import { compareChromosomes } from '../../lib/chromosomes';
import type {
  SharedStructuralVariantCountsOut,
  StructuralVariantSampleCountsOut,
  VariantLengthOut,
} from '../../lib/apiSchema.generated';
import { VariantSummarySectionState } from './VariantSummarySectionState';

/**
 * The variant summary's structural variants: every SV caller's calls, HiFiCNV's copy-number
 * calls included. Per sample by type, per chromosome and type, shared between members, and
 * their sizes.
 */

export const VARIANT_LIMIT = 100000;

// Static histogram bins — hoisted out of the component so they are not
// re-allocated on every render.
const variantBinEdges = [
  -0.5,
  0.5,
  10.5,
  100,
  1000,
  10000,
  100000,
  1000000,
  Number.POSITIVE_INFINITY,
];
const variantBinLabels = [
  '0',
  '1-10',
  '10-100',
  '100-1k',
  '1k-10k',
  '10k-100k',
  '100k-1M',
  '>1M',
];

const VariantSummaryStructuralVariants: React.FC<{ familyId: string }> = ({
  familyId,
}) => {
  const lengths = useQuery<VariantLengthOut[]>({
    queryKey: ['family', familyId, 'structural-variant-lengths'],
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/structural-variant-lengths`,
        {
          params: { limit: VARIANT_LIMIT },
        }
      );
      return res.data as VariantLengthOut[];
    },
  });
  const shared = useQuery<SharedStructuralVariantCountsOut>({
    queryKey: ['family', familyId, 'shared-structural-variant-counts'],
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/shared-structural-variant-counts`
      );
      return res.data as SharedStructuralVariantCountsOut;
    },
  });
  const perSample = useQuery<StructuralVariantSampleCountsOut[]>({
    queryKey: ['family', familyId, 'structural-variant-sample-counts'],
    queryFn: async () => {
      const res = await api.get(
        apiPath`/families/${familyId}/structural-variant-sample-counts`
      );
      return res.data as StructuralVariantSampleCountsOut[];
    },
  });
  const [logScale, setLogScale] = React.useState(true);
  const data = lengths.data;
  const sharedCounts = shared.data;
  const sampleCounts = perSample.data;

  // The O(n) aggregation over up to VARIANT_LIMIT (100k) rows runs once per data
  // load — not on every render. logScale only affects histogram rendering, so it
  // is intentionally not a dependency here.
  const summary = React.useMemo(() => {
    const rows = data ?? [];
    const allLengths = rows.map((v) => Math.abs(v.length));
    const byType: Record<string, number[]> = {};
    const bySource: Record<string, number[]> = {};
    const byChromType: Record<string, Record<string, number>> = {};
    const chromosomes = Array.from(
      new Set(rows.map((v) => v.chr || 'unknown'))
    ).sort(compareChromosomes);
    rows.forEach((v) => {
      const t = v.type || 'unknown';
      const c = v.chr || 'unknown';
      byType[t] = byType[t] || [];
      byType[t].push(Math.abs(v.length));
      bySource[v.source || 'unknown'] = bySource[v.source || 'unknown'] || [];
      bySource[v.source || 'unknown'].push(Math.abs(v.length));
      byChromType[c] = byChromType[c] || {};
      byChromType[c][t] = (byChromType[c][t] || 0) + 1;
    });
    const variantTypes = Object.keys(byType).sort();
    const totalsByChrom: Record<string, number> = {};
    chromosomes.forEach((chr) => {
      totalsByChrom[chr] = Object.values(byChromType[chr] || {}).reduce(
        (sum, v) => sum + v,
        0
      );
    });
    return {
      allLengths,
      byType,
      bySource,
      byChromType,
      chromosomes,
      variantTypes,
      totalsByChrom,
    };
  }, [data]);

  // The matrix has no totals. Each cell counts SVs per pair of carriers, so an SV
  // shared by three samples is in three cells: a row sum is not what that sample
  // carries, and the grand sum is not the number of SVs.
  const sampleNames = React.useMemo(
    () => Object.keys(sharedCounts ?? {}).sort(),
    [sharedCounts]
  );
  const sampleTypes = React.useMemo(
    () =>
      Array.from(
        new Set((sampleCounts ?? []).flatMap((row) => Object.keys(row.by_type)))
      ).sort(),
    [sampleCounts]
  );

  // Half a summary is not shown: the counts wait for every request of the section.
  const failed = lengths.error ?? shared.error ?? perSample.error;
  if (!data || !sharedCounts || !sampleCounts || failed) {
    return (
      <section id="structural-variants" className="analysis-panel space-y-4">
        <h2 className="section-title">Structural variants</h2>
        <VariantSummarySectionState
          what="structural-variant summary"
          loading={lengths.isLoading || shared.isLoading || perSample.isLoading}
          error={failed}
          onRetry={() => {
            if (lengths.error) void lengths.refetch();
            if (shared.error) void shared.refetch();
            if (perSample.error) void perSample.refetch();
          }}
        />
      </section>
    );
  }

  const {
    allLengths,
    byType,
    bySource,
    byChromType,
    chromosomes,
    variantTypes,
    totalsByChrom,
  } = summary;

  return (
    <section id="structural-variants" className="analysis-panel space-y-4">
      <h2 className="section-title">Structural variants</h2>
      <p className="analysis-count">
        Total variants: {data.length}
        {data.length === VARIANT_LIMIT && ` (showing first ${VARIANT_LIMIT})`}.
        From every SV caller, HiFiCNV’s copy-number calls included.
      </p>

      <h3 className="font-semibold">Per sample and type</h3>
      <div className="analysis-results-card overflow-x-auto">
        <table
          className="analysis-table table-sticky"
          aria-label="Structural variants per sample and type"
        >
          <thead>
            <tr>
              <th>Sample</th>
              {sampleTypes.map((type) => (
                <th key={type} className="text-center">
                  {type}
                </th>
              ))}
              <th className="text-center">Total</th>
            </tr>
          </thead>
          <tbody>
            {sampleCounts.map((row) => (
              <tr key={row.sample_id}>
                <td>{row.sample_id}</td>
                {sampleTypes.map((type) => (
                  <td key={type} className="text-center">
                    {row.by_type[type] ?? 0}
                  </td>
                ))}
                <td className="text-center">{row.total}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="font-semibold">Per chromosome and type</h3>
      <div className="analysis-results-card overflow-x-auto">
        <table
          className="analysis-table table-sticky"
          aria-label="Structural variants per chromosome and type"
        >
          <thead>
            <tr>
              <th>Chromosome</th>
              {variantTypes.map((type) => (
                <th key={type} className="text-center">
                  {type}
                </th>
              ))}
              <th className="text-center">Total</th>
            </tr>
          </thead>
          <tbody>
            {chromosomes.map((chr) => (
              <tr key={chr}>
                <td>{chr}</td>
                {variantTypes.map((type) => (
                  <td key={type} className="text-center">
                    {(byChromType[chr] && byChromType[chr][type]) || 0}
                  </td>
                ))}
                <td className="text-center">{totalsByChrom[chr]}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 className="font-semibold">Shared and unique variants</h3>
      <div className="analysis-results-card overflow-x-auto">
        <table
          className="analysis-table table-sticky"
          aria-label="Shared and unique structural variants"
        >
          <thead>
            <tr>
              <th>Sample</th>
              {sampleNames.map((name) => (
                <th key={name} className="text-center">
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sampleNames.map((row) => (
              <tr key={row}>
                <td>{row}</td>
                {sampleNames.map((col) => (
                  <td key={col} className="text-center">
                    {sharedCounts[row][col] ?? 0}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="analysis-count">
        Diagonal counts denote variants unique to the individual; off-diagonal
        counts show variants the two samples share. A variant shared by three or
        more samples is counted in every pair, so the cells do not add up to a
        total.
      </p>

      {/* The scale of the length histograms below (all variants, by type, by caller), above them
          rather than in the page's header. */}
      <div className="analysis-graph-toolbar">
        <span className="stat-label">Length histograms</span>
        <button
          type="button"
          onClick={() => setLogScale((s) => !s)}
          className="button-ghost"
        >
          {logScale ? 'Linear scale' : 'Log scale'}
        </button>
      </div>

      <div className="viz-panel space-y-2">
        <h3 className="font-semibold">All lengths</h3>
        <Histogram
          subject="SV lengths"
          data={allLengths}
          binEdges={variantBinEdges}
          binLabels={variantBinLabels}
          logScale={logScale}
        />
      </div>

      <h3 className="font-semibold">Lengths by type</h3>
      <div className="variant-summary-grid">
        {Object.entries(byType).map(([type, typeLengths]) => (
          <div
            key={type}
            className="viz-panel space-y-2"
            data-histogram-group="type"
          >
            <h4 className="font-semibold">{type}</h4>
            <Histogram
              subject={`${type} lengths`}
              data={typeLengths}
              binEdges={variantBinEdges}
              binLabels={variantBinLabels}
              logScale={logScale}
            />
          </div>
        ))}
      </div>

      <h3 className="font-semibold">Lengths by caller</h3>
      <div className="variant-summary-grid">
        {Object.entries(bySource).map(([source, sourceLengths]) => (
          <div
            key={source}
            className="viz-panel space-y-2"
            data-histogram-group="caller"
          >
            <h4 className="font-semibold">{source}</h4>
            <Histogram
              subject={`SV lengths from ${source}`}
              data={sourceLengths}
              binEdges={variantBinEdges}
              binLabels={variantBinLabels}
              logScale={logScale}
            />
          </div>
        ))}
      </div>
    </section>
  );
};

export default VariantSummaryStructuralVariants;
