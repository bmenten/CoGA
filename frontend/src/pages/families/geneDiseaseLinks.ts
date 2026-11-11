import { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import type { GeneDiseaseLinksOut, GeneDiseaseMonarchOut } from '../../lib/apiSchema.generated';

export type GeneDiseaseLinks = GeneDiseaseLinksOut;

// How many diseases a card's top line names; the card's "More annotations & detail" lists
// every link.
export const SUMMARY_DISEASE_COUNT = 3;

/**
 * What a card's top line names the diseases from, strongest first: Monarch's causal links;
 * else the gene reference's OMIM diseases; else Monarch's links that are associations only,
 * which the line says are not causal.
 */
export type GeneDiseaseSummaryBasis = 'causal' | 'omim' | 'associated';

export interface GeneDiseaseSummary {
  // null when the gene has no link at all.
  basis: GeneDiseaseSummaryBasis | null;
  names: string[];
  // The links of the same basis the line leaves out.
  more: number;
}

export const geneDiseaseKey = (symbol?: string | null) => (symbol ?? '').trim().toUpperCase();

const uniqueNames = (labels: string[]) => {
  const seen = new Set<string>();
  return labels.filter((label) => {
    const key = label.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

// Monarch's causal links, the ones more sources back first (ClinGen, OMIM and Orphanet agreeing
// on Marfan syndrome before a single-source entry), in the server's order otherwise.
const rankCausal = (entries: GeneDiseaseMonarchOut[]) =>
  entries
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => right.entry.sources.length - left.entry.sources.length || left.index - right.index)
    .map(({ entry }) => entry.disease_label);

export const summarizeGeneDiseases = (links: GeneDiseaseLinks): GeneDiseaseSummary => {
  const causal = uniqueNames(rankCausal(links.monarch.filter((entry) => entry.causal)));
  const omim = uniqueNames(links.omim.map((entry) => entry.label));
  const associated = uniqueNames(links.monarch.filter((entry) => !entry.causal).map((entry) => entry.disease_label));
  const [basis, labels]: [GeneDiseaseSummaryBasis | null, string[]] = causal.length
    ? ['causal', causal]
    : omim.length
      ? ['omim', omim]
      : associated.length
        ? ['associated', associated]
        : [null, []];
  const names = labels.slice(0, SUMMARY_DISEASE_COUNT);
  return { basis, names, more: labels.length - names.length };
};

/**
 * The gene–disease links of every gene on a page of variant cards, in one request.
 *
 * A card's top line and its expanded detail both read this, so the two never disagree. The
 * key holds the page's genes, so a new page reads its own genes and never shows the previous
 * page's links in the meantime.
 */
export const useGeneDiseaseLinks = (symbols: Array<string | null | undefined>) => {
  const asked = Array.from(
    new Map(
      symbols
        .map((symbol) => (symbol ?? '').trim())
        .filter(Boolean)
        .map((symbol) => [geneDiseaseKey(symbol), symbol] as const),
    ).values(),
  ).sort();
  const query = useQuery<GeneDiseaseLinks[]>({
    queryKey: ['gene-disease-links', asked],
    enabled: asked.length > 0,
    staleTime: 5 * 60 * 1000,
    queryFn: async () => {
      const { data } = await api.get('/genes/disease-links', {
        params: { symbol: asked },
        // symbol=A&symbol=B, the list FastAPI reads.
        paramsSerializer: { indexes: null },
      });
      // Anything but the list is a failed lookup, said as such on the cards, not a page of
      // genes without diseases.
      if (!Array.isArray(data)) throw new Error('The gene–disease lookup gave no list');
      return data as GeneDiseaseLinks[];
    },
  });
  const bySymbol = useMemo(
    () => new Map((query.data ?? []).map((entry) => [geneDiseaseKey(entry.symbol), entry])),
    [query.data],
  );
  return { bySymbol, failed: query.isError, retry: query.refetch };
};
