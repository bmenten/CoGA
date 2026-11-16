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

// A disease the top line names, with the page it links to: Monarch's for a Monarch link, OMIM's
// for an OMIM disease (null when the gene reference gives no OMIM number).
export interface GeneDiseaseSummaryEntry {
  label: string;
  href: string | null;
}

export interface GeneDiseaseSummary {
  // null when the gene has no link at all.
  basis: GeneDiseaseSummaryBasis | null;
  diseases: GeneDiseaseSummaryEntry[];
  // The links of the same basis the line leaves out.
  more: number;
}

export const geneDiseaseKey = (symbol?: string | null) => (symbol ?? '').trim().toUpperCase();

// The first entry of each name, whatever its case.
const uniqueByLabel = (entries: GeneDiseaseSummaryEntry[]) => {
  const seen = new Set<string>();
  return entries.filter((entry) => {
    const key = entry.label.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });
};

const monarchEntry = (entry: GeneDiseaseMonarchOut): GeneDiseaseSummaryEntry => ({
  label: entry.disease_label,
  href: entry.monarch_url,
});

// Monarch's causal links, the ones more sources back first (ClinGen, OMIM and Orphanet agreeing
// on Marfan syndrome before a single-source entry), in the server's order otherwise.
const rankCausal = (entries: GeneDiseaseMonarchOut[]) =>
  entries
    .map((entry, index) => ({ entry, index }))
    .sort((left, right) => right.entry.sources.length - left.entry.sources.length || left.index - right.index)
    .map(({ entry }) => monarchEntry(entry));

export const summarizeGeneDiseases = (links: GeneDiseaseLinks): GeneDiseaseSummary => {
  const causal = uniqueByLabel(rankCausal(links.monarch.filter((entry) => entry.causal)));
  const omim = uniqueByLabel(links.omim.map((entry) => ({ label: entry.label, href: entry.href })));
  const associated = uniqueByLabel(links.monarch.filter((entry) => !entry.causal).map(monarchEntry));
  const [basis, entries]: [GeneDiseaseSummaryBasis | null, GeneDiseaseSummaryEntry[]] = causal.length
    ? ['causal', causal]
    : omim.length
      ? ['omim', omim]
      : associated.length
        ? ['associated', associated]
        : [null, []];
  const diseases = entries.slice(0, SUMMARY_DISEASE_COUNT);
  return { basis, diseases, more: entries.length - diseases.length };
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
