import React from 'react';
import { useParams, Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import api from '../../lib/api';
import Histogram from '../../components/visualizations/Histogram';
import { compareChromosomes } from '../../lib/chromosomes';
import PageState from '../../components/PageState';

const VARIANT_LIMIT = 100000;

interface VariantLength {
  length: number;
  type: string;
  source?: string | null;
  chr: string;
}

type SharedVariantCounts = Record<string, Record<string, number>>;

const FamilyVariantSummaryPage: React.FC = () => {
  const { familyId } = useParams<{ familyId: string }>();
  const { data, isLoading } = useQuery<VariantLength[]>({
    queryKey: ['family', familyId, 'structural-variant-lengths'],
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/structural-variant-lengths`, {
        params: { limit: VARIANT_LIMIT },
      });
      return res.data as VariantLength[];
    },
  });

  const { data: sharedCounts, isLoading: isLoadingShared } =
    useQuery<SharedVariantCounts>({
      queryKey: ['family', familyId, 'shared-structural-variant-counts'],
      queryFn: async () => {
        const res = await api.get(
          `/families/${familyId}/shared-structural-variant-counts`
        );
        return res.data as SharedVariantCounts;
      },
    });
  const [logScale, setLogScale] = React.useState(true);

  if (isLoading || isLoadingShared) {
    return (
      <PageState
        kicker="Summary"
        title="Loading variant summary"
        message="Calculating counts, sharing, and length distributions for this family."
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

  const allLengths = data.map((v) => Math.abs(v.length));
  const byType: Record<string, number[]> = {};
  const bySource: Record<string, number[]> = {};
  const byTypeChrom: Record<string, Record<string, number>> = {};
  const byChromType: Record<string, Record<string, number>> = {};
  const chromosomes = Array.from(
    new Set(data.map((v) => v.chr || 'unknown'))
  ).sort(compareChromosomes);
  const sampleNames = Object.keys(sharedCounts).sort();
  data.forEach((v) => {
    const t = v.type || 'unknown';
    const c = v.chr || 'unknown';
    byType[t] = byType[t] || [];
    byType[t].push(Math.abs(v.length));
    bySource[v.source || 'unknown'] = bySource[v.source || 'unknown'] || [];
    bySource[v.source || 'unknown'].push(Math.abs(v.length));
    byTypeChrom[t] = byTypeChrom[t] || {};
    byTypeChrom[t][c] = (byTypeChrom[t][c] || 0) + 1;
    byChromType[c] = byChromType[c] || {};
    byChromType[c][t] = (byChromType[c][t] || 0) + 1;
  });
  const variantTypes = Object.keys(byTypeChrom).sort();
  const totalsByChrom: Record<string, number> = {};
  chromosomes.forEach((chr) => {
    totalsByChrom[chr] = Object.values(byChromType[chr] || {}).reduce(
      (sum, v) => sum + v,
      0
    );
  });

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

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card">
        <div className="page-header">
          <div className="space-y-2">
            <p className="page-kicker">Summary</p>
            <h1 className="catalog-card-title">Variant summary for family {familyId}</h1>
            <p className="catalog-card-copy">
              Review counts, sharing, and length distributions.
            </p>
          </div>
          <div className="inline-actions">
            <Link to={`/families/${familyId}`} className="button-secondary hover:no-underline">
              Back to family
            </Link>
            <button
              onClick={() => setLogScale((s) => !s)}
              className="button-ghost"
            >
              {logScale ? 'Linear scale' : 'Log scale'}
            </button>
          </div>
        </div>
      </section>

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
        {(() => {
          const rowTotals: Record<string, number> = {};
          const columnTotals: Record<string, number> = {};
          sampleNames.forEach((row) => {
            rowTotals[row] = 0;
            sampleNames.forEach((col) => {
              const val = sharedCounts[row][col] ?? 0;
              rowTotals[row] += val;
              columnTotals[col] = (columnTotals[col] || 0) + val;
            });
          });
          const grandTotal = Object.values(rowTotals).reduce(
            (s, v) => s + v,
            0
          );
          return (
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
                    <th className="text-center">Total</th>
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
                      <td className="text-center">
                        {rowTotals[row]}
                      </td>
                    </tr>
                  ))}
                  <tr>
                    <td>Total</td>
                    {sampleNames.map((col) => (
                      <td key={col} className="text-center">
                        {columnTotals[col] || 0}
                      </td>
                    ))}
                    <td className="text-center">{grandTotal}</td>
                  </tr>
                </tbody>
              </table>
            </div>
          );
        })()}
        <p className="analysis-count">
          Diagonal counts denote variants unique to the individual; off-diagonal
          counts show shared variants.
        </p>
      </section>

      <section id="all" className="analysis-panel space-y-4">
        <h2 className="section-title">All variants</h2>
        <Histogram
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
