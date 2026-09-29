import React from 'react';
import { useParams } from 'react-router';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import Histogram from '../../components/visualizations/Histogram';
import { compareChromosomes } from '../../lib/chromosomes';
import PageState from '../../components/PageState';
import FamilyPageHeader from './FamilyPageHeader';
import { apiPath } from '../../lib/apiPath';
import { getErrorMessage } from '../../lib/errorMessage';

const VARIANT_LIMIT = 100000;

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

interface VariantLength {
  length: number;
  type: string;
  source?: string | null;
  chr: string;
}

type SharedVariantCounts = Record<string, Record<string, number>>;

const FamilyVariantSummaryPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  // For the shared header's pedigree — the other family pages already load this.
  const { data: family } = useQuery<{ pedigree?: string | null; members?: unknown[] }>({
    queryKey: ['family', familyId],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}`);
      return res.data;
    },
  });
  const { data, isLoading, error: lengthsError, refetch: refetchLengths } = useQuery<VariantLength[]>({
    queryKey: ['family', familyId, 'structural-variant-lengths'],
    queryFn: async () => {
      const res = await api.get(apiPath`/families/${familyId}/structural-variant-lengths`, {
        params: { limit: VARIANT_LIMIT },
      });
      return res.data as VariantLength[];
    },
  });

  const {
    data: sharedCounts,
    isLoading: isLoadingShared,
    error: sharedError,
    refetch: refetchShared,
  } = useQuery<SharedVariantCounts>({
      queryKey: ['family', familyId, 'shared-structural-variant-counts'],
      queryFn: async () => {
        const res = await api.get(
          apiPath`/families/${familyId}/shared-structural-variant-counts`
        );
        return res.data as SharedVariantCounts;
      },
    });
  const [logScale, setLogScale] = React.useState(true);

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
    return { allLengths, byType, bySource, byChromType, chromosomes, variantTypes, totalsByChrom };
  }, [data]);

  // The matrix has no totals. Each cell counts SVs per pair of carriers, so an SV
  // shared by three samples is in three cells: a row sum is not what that sample
  // carries, and the grand sum is not the number of SVs (#526).
  const sampleNames = React.useMemo(() => Object.keys(sharedCounts ?? {}).sort(), [sharedCounts]);

  if (isLoading || isLoadingShared) {
    return (
      <PageState
        loading
        kicker="Summary"
        title="Loading variant summary"
        message="Calculating counts, sharing, and length distributions for this family."
      />
    );
  }
  const loadError = lengthsError ?? sharedError;
  if (loadError) {
    return (
      <PageState
        kicker="Summary"
        title="Could not load the variant summary"
        message={`${getErrorMessage(loadError, 'The request failed').replace(/\.+$/, '')}. This is not an empty result.`}
        action={
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              if (lengthsError) void refetchLengths();
              if (sharedError) void refetchShared();
            }}
          >
            Retry
          </button>
        }
      />
    );
  }
  if (!data || !sharedCounts) {
    return (
      <PageState
        kicker="Summary"
        title="No variant summary available"
        message="There is not enough structural variant data to build the summary views."
      />
    );
  }

  const { allLengths, byType, bySource, byChromType, chromosomes, variantTypes, totalsByChrom } =
    summary;

  return (
    <div className="page-shell analysis-shell">
      <FamilyPageHeader
        kicker="Variant summary"
        familyId={familyId}
        family={family}
        actions={
          <button onClick={() => setLogScale((s) => !s)} className="button-ghost">
            {logScale ? 'Linear scale' : 'Log scale'}
          </button>
        }
      >
        <p className="catalog-card-copy">Review counts, sharing, and length distributions.</p>
      </FamilyPageHeader>

      <nav className="analysis-nav">
        <a href="#counts">
          Counts
        </a>
        <a href="#all">
          All variants
        </a>
        <a href="#sharing">
          Shared/Unique
        </a>
        <a href="#by-type">
          By type
        </a>
        <a href="#by-source">
          By source
        </a>
      </nav>

      <section id="counts" className="analysis-panel space-y-4">
        <h2 className="section-title">
          Variant counts by chromosome and type
        </h2>
        <p className="analysis-count">
          Total variants: {data.length}
          {data.length === VARIANT_LIMIT && ` (showing first ${VARIANT_LIMIT})`}
        </p>
        <div className="analysis-results-card overflow-x-auto">
          <table className="analysis-table table-sticky">
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
                  <td className="text-center">
                    {totalsByChrom[chr]}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <section id="sharing" className="analysis-panel space-y-4">
        <h2 className="section-title">Shared and unique variants</h2>
        <div className="analysis-results-card overflow-x-auto">
          <table className="analysis-table table-sticky">
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
          counts show variants the two samples share. A variant shared by three or more
          samples is counted in every pair, so the cells do not add up to a total.
        </p>
      </section>

      <section id="all" className="analysis-panel space-y-4">
        <h2 className="section-title">All variants</h2>
        <Histogram
          subject="SV lengths"
          data={allLengths}
          binEdges={variantBinEdges}
          binLabels={variantBinLabels}
          logScale={logScale}
        />
      </section>

      <section id="by-type" className="analysis-panel space-y-4">
        <h2 className="section-title">By type</h2>
        {Object.entries(byType).map(([type, lengths]) => (
          <div key={type} className="viz-panel space-y-2">
            <h3 className="font-semibold">{type}</h3>
            <Histogram
              subject={`${type} lengths`}
              data={lengths}
              binEdges={variantBinEdges}
              binLabels={variantBinLabels}
              logScale={logScale}
            />
          </div>
        ))}
      </section>

      <section id="by-source" className="analysis-panel space-y-4">
        <h2 className="section-title">By source</h2>
        {Object.entries(bySource).map(([source, lengths]) => (
          <div key={source} className="viz-panel space-y-2">
            <h3 className="font-semibold">{source}</h3>
            <Histogram
              subject={`SV lengths from ${source}`}
              data={lengths}
              binEdges={variantBinEdges}
              binLabels={variantBinLabels}
              logScale={logScale}
            />
          </div>
        ))}
      </section>
    </div>
  );
};

export default FamilyVariantSummaryPage;
