import { useEffect, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import { apiPath, raw } from '../../lib/apiPath';
import QueryFailure from '../../components/QueryFailure';
import {
  CARRIER_CLASS_LABELS,
  GNOMAD_AF_CLASS_LABELS,
  type CohortFrequencyCell,
} from './globalSmallVariantSearch';
import type {
  ExplorerFrequency,
  ExplorerGenePage,
  ExplorerTranscriptPage,
} from './types';

const VIEW_PAGE_SIZE = 100;

type ViewOrder = 'asc' | 'desc';

const sortIndicator = (active: boolean, order: ViewOrder) =>
  active ? (order === 'desc' ? ' ↓' : ' ↑') : '';

/** Families whose variants the explorer's summary does not yet hold as they are now. */
export const PendingFamiliesNotice = ({ count }: { count: number }) =>
  count > 0 ? (
    <div
      className="variant-workspace-feedback variant-workspace-feedback--warning"
      role="status"
    >
      {count} famil{count === 1 ? 'y is' : 'ies are'} being added to the
      explorer&apos;s counts or brought up to date after a change:{' '}
      {count === 1 ? 'its' : 'their'} variants may be missing, or counted as
      before the change. Refresh in a moment.
    </div>
  ) : null;

interface SortState<Sort extends string> {
  sort: Sort;
  order: ViewOrder;
  offset: number;
}

/** A view's sort and page, back to its first page whenever the filters change. */
const useViewPaging = <Sort extends string>(
  queryString: string,
  initial: Sort
) => {
  const [state, setState] = useState<SortState<Sort>>({
    sort: initial,
    order: 'desc',
    offset: 0,
  });
  useEffect(() => {
    setState((prev) => ({ ...prev, offset: 0 }));
  }, [queryString]);
  const sortBy = (next: Sort) =>
    setState((prev) =>
      prev.sort === next
        ? {
            sort: next,
            order: prev.order === 'desc' ? 'asc' : 'desc',
            offset: 0,
          }
        : { sort: next, order: 'desc', offset: 0 }
    );
  return {
    ...state,
    sortBy,
    setOffset: (offset: number) => setState((prev) => ({ ...prev, offset })),
  };
};

const ViewPagination = ({
  offset,
  total,
  fetching,
  onOffset,
}: {
  offset: number;
  total: number;
  fetching: boolean;
  onOffset: (offset: number) => void;
}) =>
  total > VIEW_PAGE_SIZE ? (
    <div className="variant-explorer-pagination">
      <button
        type="button"
        className="button-secondary"
        disabled={offset === 0 || fetching}
        onClick={() => onOffset(Math.max(0, offset - VIEW_PAGE_SIZE))}
      >
        Previous
      </button>
      <span className="table-subtle">
        {offset + 1}–{Math.min(offset + VIEW_PAGE_SIZE, total)} of{' '}
        {total.toLocaleString()}
      </span>
      <button
        type="button"
        className="button-secondary"
        disabled={offset + VIEW_PAGE_SIZE >= total || fetching}
        onClick={() => onOffset(offset + VIEW_PAGE_SIZE)}
      >
        Next
      </button>
    </div>
  ) : null;

interface ViewProps {
  queryString: string;
  enabled: boolean;
  onShowGene: (gene: string) => void;
}

type GeneSort = 'variants' | 'carriers' | 'gene';

/** One row per gene the filtered variants' annotations name (owner's choice of 2026-11-12):
 * the variants, by their most severe impact, and their carriers summed over them. */
export const ExplorerGeneView = ({
  queryString,
  enabled,
  onShowGene,
}: ViewProps) => {
  const paging = useViewPaging<GeneSort>(queryString, 'variants');
  const params = new URLSearchParams(queryString);
  params.set('sort', paging.sort);
  params.set('order', paging.order);
  params.set('offset', String(paging.offset));
  params.set('page_size', String(VIEW_PAGE_SIZE));
  const { data, isLoading, isError, error, refetch, isFetching } =
    useQuery<ExplorerGenePage>({
      queryKey: ['variant-explorer', 'genes', params.toString()],
      enabled,
      queryFn: async () => {
        const res = await api.get(
          apiPath`/variant-explorer/small-variants/genes?${raw(params.toString())}`
        );
        return res.data as ExplorerGenePage;
      },
      placeholderData: keepPreviousData,
    });
  if (isLoading) return <p className="table-subtle">Loading genes…</p>;
  if (isError)
    return (
      <QueryFailure
        what="the genes"
        error={error}
        onRetry={() => void refetch()}
      />
    );
  const genes = data?.genes ?? [];
  return (
    <div className="space-y-4">
      <PendingFamiliesNotice count={data?.pending_families ?? 0} />
      <p className="analysis-section-title">
        {(data?.total ?? 0).toLocaleString()} gene{data?.total === 1 ? '' : 's'}
        {isFetching ? ' · updating…' : ''}
      </p>
      <p className="table-subtle">
        Carriers are summed over a gene&apos;s variants: a sample with two
        variants in the gene counts twice. Open a gene for its variants.
      </p>
      {genes.length === 0 ? (
        <p className="table-subtle">
          No gene: no filtered variant is annotated with one.
        </p>
      ) : (
        <div className="analysis-results-card overflow-x-auto">
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                <th
                  className="table-sortable"
                  onClick={() => paging.sortBy('gene')}
                >
                  Gene{sortIndicator(paging.sort === 'gene', paging.order)}
                </th>
                <th
                  className="table-sortable table-mono"
                  onClick={() => paging.sortBy('variants')}
                >
                  Variants
                  {sortIndicator(paging.sort === 'variants', paging.order)}
                </th>
                <th className="table-mono">High</th>
                <th className="table-mono">Moderate</th>
                <th className="table-mono">Low</th>
                <th className="table-mono">Modifier</th>
                <th
                  className="table-sortable table-mono"
                  onClick={() => paging.sortBy('carriers')}
                >
                  Carriers, summed
                  {sortIndicator(paging.sort === 'carriers', paging.order)}
                </th>
                <th className="table-mono">Het</th>
                <th className="table-mono">Hom</th>
              </tr>
            </thead>
            <tbody>
              {genes.map((row) => (
                <tr key={row.gene}>
                  <td>
                    <button
                      type="button"
                      className="table-link"
                      onClick={() => onShowGene(row.gene)}
                    >
                      {row.gene}
                    </button>
                  </td>
                  <td className="table-mono">{row.variants}</td>
                  <td className="table-mono">{row.high}</td>
                  <td className="table-mono">{row.moderate}</td>
                  <td className="table-mono">{row.low}</td>
                  <td className="table-mono">{row.modifier}</td>
                  <td className="table-mono">{row.carriers}</td>
                  <td className="table-mono">{row.het}</td>
                  <td className="table-mono">{row.hom}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ViewPagination
        offset={paging.offset}
        total={data?.total ?? 0}
        fetching={isFetching}
        onOffset={paging.setOffset}
      />
    </div>
  );
};

type TranscriptSort = 'variants' | 'carriers' | 'transcript' | 'gene';
type TranscriptSelection = 'all' | 'mane' | 'canonical';

const TRANSCRIPT_SELECTIONS: Record<TranscriptSelection, string> = {
  all: 'All transcripts',
  mane: 'MANE (Select or Plus Clinical)',
  canonical: 'Canonical',
};

/** One row per transcript the filtered variants' annotations name, every transcript or the
 * MANE or canonical ones: the variants by their most severe consequence class on it. */
export const ExplorerTranscriptView = ({
  queryString,
  enabled,
  onShowGene,
}: ViewProps) => {
  const paging = useViewPaging<TranscriptSort>(queryString, 'variants');
  const [selection, setSelection] = useState<TranscriptSelection>('all');
  const params = new URLSearchParams(queryString);
  params.set('transcripts', selection);
  params.set('sort', paging.sort);
  params.set('order', paging.order);
  params.set('offset', String(paging.offset));
  params.set('page_size', String(VIEW_PAGE_SIZE));
  const { data, isLoading, isError, error, refetch, isFetching } =
    useQuery<ExplorerTranscriptPage>({
      queryKey: ['variant-explorer', 'transcripts', params.toString()],
      enabled,
      queryFn: async () => {
        const res = await api.get(
          apiPath`/variant-explorer/small-variants/transcripts?${raw(params.toString())}`
        );
        return res.data as ExplorerTranscriptPage;
      },
      placeholderData: keepPreviousData,
    });
  if (isLoading) return <p className="table-subtle">Loading transcripts…</p>;
  if (isError)
    return (
      <QueryFailure
        what="the transcripts"
        error={error}
        onRetry={() => void refetch()}
      />
    );
  const rows = data?.rows ?? [];
  return (
    <div className="space-y-4">
      <PendingFamiliesNotice count={data?.pending_families ?? 0} />
      <div className="variant-explorer-results-header">
        <p className="analysis-section-title">
          {(data?.total ?? 0).toLocaleString()} transcript
          {data?.total === 1 ? '' : 's'}
          {isFetching ? ' · updating…' : ''}
        </p>
        <label className="variant-explorer-assembly-row">
          <span className="variant-explorer-assembly-label">Transcripts</span>
          <select
            value={selection}
            onChange={(event) => {
              setSelection(event.target.value as TranscriptSelection);
              paging.setOffset(0);
            }}
          >
            {(Object.keys(TRANSCRIPT_SELECTIONS) as TranscriptSelection[]).map(
              (key) => (
                <option key={key} value={key}>
                  {TRANSCRIPT_SELECTIONS[key]}
                </option>
              )
            )}
          </select>
        </label>
      </div>
      <p className="table-subtle">
        Each variant counts once per transcript, under its most severe
        consequence on it; carriers are summed over the transcript&apos;s
        variants. Open a gene for its variants.
      </p>
      {rows.length === 0 ? (
        <p className="table-subtle">
          No transcript: no filtered variant is annotated on one.
        </p>
      ) : (
        <div className="analysis-results-card overflow-x-auto">
          <table className="analysis-table table-sticky">
            <thead>
              <tr>
                <th
                  className="table-sortable"
                  onClick={() => paging.sortBy('transcript')}
                >
                  Transcript
                  {sortIndicator(paging.sort === 'transcript', paging.order)}
                </th>
                <th
                  className="table-sortable"
                  onClick={() => paging.sortBy('gene')}
                >
                  Gene{sortIndicator(paging.sort === 'gene', paging.order)}
                </th>
                <th>MANE / canonical</th>
                <th
                  className="table-sortable table-mono"
                  onClick={() => paging.sortBy('variants')}
                >
                  Variants
                  {sortIndicator(paging.sort === 'variants', paging.order)}
                </th>
                <th className="table-mono">LoF</th>
                <th className="table-mono">Missense / in-frame</th>
                <th className="table-mono">Splice region</th>
                <th className="table-mono">Synonymous</th>
                <th className="table-mono">Other</th>
                <th
                  className="table-sortable table-mono"
                  onClick={() => paging.sortBy('carriers')}
                >
                  Carriers, summed
                  {sortIndicator(paging.sort === 'carriers', paging.order)}
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.transcript_id}>
                  <td className="table-mono">{row.transcript_id}</td>
                  <td>
                    {row.gene ? (
                      <button
                        type="button"
                        className="table-link"
                        onClick={() => onShowGene(row.gene ?? '')}
                      >
                        {row.gene}
                      </button>
                    ) : (
                      <span className="table-empty">—</span>
                    )}
                  </td>
                  <td>
                    {[
                      row.mane ? 'MANE' : null,
                      row.canonical ? 'canonical' : null,
                    ]
                      .filter(Boolean)
                      .join(', ') || <span className="table-empty">—</span>}
                  </td>
                  <td className="table-mono">{row.variants}</td>
                  <td className="table-mono">{row.lof}</td>
                  <td className="table-mono">{row.missense}</td>
                  <td className="table-mono">{row.splice}</td>
                  <td className="table-mono">{row.synonymous}</td>
                  <td className="table-mono">{row.other}</td>
                  <td className="table-mono">{row.carriers}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <ViewPagination
        offset={paging.offset}
        total={data?.total ?? 0}
        fetching={isFetching}
        onOffset={paging.setOffset}
      />
    </div>
  );
};

/** The filtered variants by their carriers in the cohort against their gnomAD frequency
 * (owner's choice of 2026-11-12); a cell opens its variants. */
export const ExplorerFrequencyView = ({
  queryString,
  enabled,
  onShowCell,
}: {
  queryString: string;
  enabled: boolean;
  onShowCell: (cell: CohortFrequencyCell) => void;
}) => {
  const { data, isLoading, isError, error, refetch, isFetching } =
    useQuery<ExplorerFrequency>({
      queryKey: ['variant-explorer', 'frequency', queryString],
      enabled,
      queryFn: async () => {
        const res = await api.get(
          apiPath`/variant-explorer/small-variants/frequency?${raw(queryString)}`
        );
        return res.data as ExplorerFrequency;
      },
      placeholderData: keepPreviousData,
    });
  if (isLoading)
    return <p className="table-subtle">Loading the cohort frequencies…</p>;
  if (isError) {
    return (
      <QueryFailure
        what="the cohort frequencies"
        error={error}
        onRetry={() => void refetch()}
      />
    );
  }
  const carrierClasses = data?.carrier_classes ?? [];
  const afClasses = data?.gnomad_af_classes ?? [];
  const cells = new Map(
    (data?.cells ?? []).map((cell) => [
      `${cell.carrier_class}|${cell.gnomad_af_class}`,
      cell,
    ])
  );
  return (
    <div className="space-y-4">
      <PendingFamiliesNotice count={data?.pending_families ?? 0} />
      <p className="analysis-section-title">
        Cohort frequency{isFetching ? ' · updating…' : ''}
      </p>
      <p className="table-subtle">
        The filtered variants by their carriers in your projects (rows) and
        their gnomAD allele frequency (columns). Variants frequent here and rare
        in gnomAD can be artefacts or founder variants. Open a cell for its
        variants.
      </p>
      <div className="analysis-results-card overflow-x-auto">
        <table className="analysis-table">
          <thead>
            <tr>
              <th>Carriers</th>
              {afClasses.map((afClass) => (
                <th key={afClass} className="table-mono">
                  {GNOMAD_AF_CLASS_LABELS[afClass] ?? afClass}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {carrierClasses.map((carrierClass) => (
              <tr key={carrierClass}>
                <th scope="row">
                  {CARRIER_CLASS_LABELS[carrierClass] ?? carrierClass}
                </th>
                {afClasses.map((afClass) => {
                  const cell = cells.get(`${carrierClass}|${afClass}`);
                  const variants = cell?.variants ?? 0;
                  return (
                    <td key={afClass} className="table-mono">
                      {cell && variants > 0 ? (
                        <button
                          type="button"
                          className="table-link variant-explorer-count-button"
                          onClick={() =>
                            onShowCell({
                              minSamples: cell.carrier_min,
                              maxSamples: cell.carrier_max ?? null,
                              gnomadAfClass: cell.gnomad_af_class,
                            })
                          }
                        >
                          {variants.toLocaleString()}
                        </button>
                      ) : (
                        <span className="table-empty">0</span>
                      )}
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
