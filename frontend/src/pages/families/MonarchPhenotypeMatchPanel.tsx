import { useState } from 'react';
import { Link } from 'react-router';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';

interface PhenotypeTermRef {
  hpo_id: string;
  label?: string | null;
}

interface PhenotypeMatchResult {
  rank: number;
  score?: number | null;
  id: string;
  name: string;
  category?: string | null;
  symbol?: string | null;
  gene_in_platform?: boolean;
  matching_phenotypes?: PhenotypeTermRef[];
  extra_phenotypes?: PhenotypeTermRef[];
}

interface FamilyPhenotypeMatch {
  group: string;
  sample_id?: string | null;
  query_hpo_ids: string[];
  results: PhenotypeMatchResult[];
  source: string;
}

type Props = {
  familyId?: string;
  projectId?: string;
};

const EXTRA_PREVIEW_COUNT = 8;
// Monarch's semsim search caps the request at 50 candidates, so this is the most
// the live ranking can return.
const GENE_LIMIT = 50;

const buildGeneHref = (symbol: string, familyId?: string, projectId?: string) => {
  const params = new URLSearchParams({ gene: symbol });
  if (familyId) params.set('family_id', familyId);
  if (projectId) params.set('project_id', projectId);
  return `/genes?${params.toString()}`;
};

function TermChips({
  terms,
  tone,
  truncateTo,
}: {
  terms: PhenotypeTermRef[];
  tone: 'success' | 'neutral';
  truncateTo?: number;
}) {
  const [expanded, setExpanded] = useState(false);

  if (terms.length === 0) {
    return <span className="dashboard-link-note">—</span>;
  }

  const collapsed = truncateTo !== undefined && !expanded && terms.length > truncateTo;
  const shown = collapsed ? terms.slice(0, truncateTo) : terms;

  return (
    <div className="phenotype-chip-list">
      {shown.map((term) => (
        <span
          key={term.hpo_id}
          className={`table-chip phenotype-chip table-chip--${tone}`}
          title={term.hpo_id}
        >
          {term.label || term.hpo_id}
        </span>
      ))}
      {truncateTo !== undefined && terms.length > truncateTo ? (
        <button
          type="button"
          className="button-ghost phenotype-chip-toggle"
          onClick={() => setExpanded((value) => !value)}
        >
          {collapsed ? `+${terms.length - truncateTo} more` : 'Show fewer'}
        </button>
      ) : null}
    </div>
  );
}

export default function MonarchPhenotypeMatchPanel({ familyId, projectId }: Props) {
  const [enabled, setEnabled] = useState(false);

  const { data, isFetching, isError } = useQuery<FamilyPhenotypeMatch>({
    queryKey: ['family', familyId, 'phenotype-match'],
    enabled: Boolean(familyId) && enabled,
    staleTime: 1000 * 60 * 30,
    queryFn: async () => {
      const res = await api.get(`/families/${familyId}/phenotype-match`, {
        params: { group: 'Human Genes', limit: GENE_LIMIT },
      });
      return res.data as FamilyPhenotypeMatch;
    },
  });

  const hasRun = enabled && !isFetching && !isError && data !== undefined;

  return (
    <section className="surface-card space-y-3">
      <div className="family-workspace-card-head">
        <div className="space-y-1">
          <h2 className="section-title">Phenotype match (Monarch)</h2>
          <p className="dashboard-link-note">
            Rank candidate genes by phenotypic similarity to this family&apos;s observed
            HPO terms, via the Monarch Initiative semantic-similarity service.
          </p>
        </div>
        <button
          type="button"
          className="btn btn-secondary"
          onClick={() => setEnabled(true)}
          disabled={isFetching}
        >
          {isFetching ? 'Matching…' : hasRun ? 'Re-run match' : 'Find candidate genes'}
        </button>
      </div>

      {isError ? (
        <p className="dashboard-link-note">
          Monarch phenotype matching is currently unavailable. Try again later.
        </p>
      ) : null}

      {hasRun && data ? (
        data.results.length === 0 ? (
          <p className="dashboard-link-note">
            {data.query_hpo_ids.length === 0
              ? 'No present HPO phenotypes recorded for this family yet — add observed phenotypes to rank candidate genes.'
              : 'No phenotype matches were returned.'}
          </p>
        ) : (
          <>
            <p className="dashboard-link-note">
              Top {data.results.length} candidate gene{data.results.length === 1 ? '' : 's'},
              ranked from {data.query_hpo_ids.length} observed phenotype
              {data.query_hpo_ids.length === 1 ? '' : 's'}
              {data.results.length >= GENE_LIMIT
                ? ` (Monarch returns at most ${GENE_LIMIT}).`
                : '.'}
            </p>
            <div className="data-table-shell overflow-x-auto">
              <table className="analysis-table table-sticky">
                <thead>
                  <tr>
                    <th>Gene</th>
                    <th>Score</th>
                    <th>Matching HPO terms</th>
                    <th>Extra HPO terms</th>
                  </tr>
                </thead>
                <tbody>
                  {data.results.map((result) => (
                    <tr key={result.id}>
                      <td>
                        {result.symbol && result.gene_in_platform ? (
                          <Link
                            className="gene-compact-link"
                            to={buildGeneHref(result.symbol, familyId, projectId)}
                          >
                            {result.symbol}
                          </Link>
                        ) : (
                          <span>
                            {result.name}
                            {result.symbol && !result.gene_in_platform ? (
                              <span className="dashboard-link-note"> (not in platform)</span>
                            ) : null}
                          </span>
                        )}
                      </td>
                      <td>
                        {typeof result.score === 'number' ? result.score.toFixed(2) : '—'}
                      </td>
                      <td>
                        <TermChips terms={result.matching_phenotypes ?? []} tone="success" />
                      </td>
                      <td>
                        <TermChips
                          terms={result.extra_phenotypes ?? []}
                          tone="neutral"
                          truncateTo={EXTRA_PREVIEW_COUNT}
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )
      ) : null}
    </section>
  );
}
