import { useEffect, useMemo, useState } from 'react';
import { keepPreviousData, useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import SmallVariantFilterForm from '../families/SmallVariantFilterForm';
import type {
  GenePanel,
  SmallVariantFilterPreset,
  SmallVariantTagDefinition,
} from '../families/smallVariantSearch';
import GlobalSmallVariantTable from './GlobalSmallVariantTable';
import VariantCarrierModal from './VariantCarrierModal';
import {
  useGlobalSmallVariantSearchState,
  type GenotypeFilterMode,
  type SampleGenotypeFilter,
} from './globalSmallVariantSearch';
import type {
  CarrierModalMode,
  GlobalVariantPage,
  GlobalVariantRow,
  VariantExplorerAssembly,
} from './types';
import { apiPath, raw } from '../../lib/apiPath';

const EMPTY_PANELS: GenePanel[] = [];
const EMPTY_PRESETS: SmallVariantFilterPreset[] = [];

const GENOTYPE_MODE_LABELS: Record<GenotypeFilterMode, string> = {
  het: 'Het',
  hom: 'Hom',
  het_hom: 'Het + Hom',
};

const noopSavePreset = async () => {};

const GlobalSmallVariantExplorerPage = () => {
  const search = useGlobalSmallVariantSearchState();
  const {
    assemblyId,
    setAssemblyId,
    requestQueryString,
    pageNumber,
    goToNextPage,
    goToPreviousPage,
    sort,
    order,
    setSort,
    includeImputed,
    setIncludeImputed,
    applyGenotypeFilters,
    genotypeFilters,
  } = search;

  // Draft rows for the per-sample genotype filter (applied on "Apply").
  const [genotypeRows, setGenotypeRows] = useState<SampleGenotypeFilter[]>([]);
  const [newSample, setNewSample] = useState('');

  const [carrierModal, setCarrierModal] = useState<{
    variant: GlobalVariantRow;
    mode: CarrierModalMode;
  } | null>(null);

  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);

  const handleDownloadCsv = async () => {
    setExportError(null);
    setIsExporting(true);
    try {
      const res = await api.get(apiPath`/variant-explorer/small-variants/export?${raw(requestQueryString)}`, {
        responseType: 'blob',
      });
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `variant-explorer-${assemblyId ?? 'export'}.csv`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.URL.revokeObjectURL(url);
    } catch {
      setExportError('Could not export variants. Try narrowing your filters and retry.');
    } finally {
      setIsExporting(false);
    }
  };

  const addGenotypeRow = () => {
    const sample = newSample.trim();
    if (!sample || genotypeRows.some((row) => row.sample === sample)) {
      setNewSample('');
      return;
    }
    setGenotypeRows((rows) => [...rows, { sample, mode: 'het_hom' }]);
    setNewSample('');
  };

  const updateGenotypeRowMode = (sample: string, mode: GenotypeFilterMode) => {
    setGenotypeRows((rows) => rows.map((row) => (row.sample === sample ? { ...row, mode } : row)));
  };

  const removeGenotypeRow = (sample: string) => {
    setGenotypeRows((rows) => rows.filter((row) => row.sample !== sample));
  };

  const genotypeFilterDirty = useMemo(
    () => JSON.stringify(genotypeRows) !== JSON.stringify(genotypeFilters),
    [genotypeRows, genotypeFilters],
  );

  const { data: assemblies } = useQuery<VariantExplorerAssembly[]>({
    queryKey: ['variant-explorer', 'assemblies'],
    queryFn: async () => {
      const res = await api.get('/variant-explorer/assemblies');
      return res.data as VariantExplorerAssembly[];
    },
  });

  // Default the assembly selector to the most common accessible assembly.
  useEffect(() => {
    if (!assemblyId && assemblies && assemblies.length > 0) {
      setAssemblyId(assemblies[0].assembly_id);
    }
  }, [assemblyId, assemblies, setAssemblyId]);

  const { data: tagDefinitions } = useQuery<SmallVariantTagDefinition[]>({
    queryKey: ['variant-explorer', 'small-variant-tags'],
    queryFn: async () => {
      const res = await api.get('/variant-explorer/small-variant-tags');
      return res.data as SmallVariantTagDefinition[];
    },
  });

  const { data: panels = EMPTY_PANELS } = useQuery<GenePanel[]>({
    queryKey: ['panels'],
    queryFn: async () => {
      const res = await api.get('/panels');
      return res.data as GenePanel[];
    },
  });

  const { data: sampleOptions } = useQuery<string[]>({
    queryKey: ['variant-explorer', 'samples', assemblyId ?? null],
    enabled: Boolean(assemblyId),
    queryFn: async () => {
      const res = await api.get('/variant-explorer/samples', {
        params: assemblyId ? { assembly_id: assemblyId } : undefined,
      });
      return res.data as string[];
    },
  });

  const {
    data: variantPage,
    isLoading,
    isError,
    isFetching,
  } = useQuery<GlobalVariantPage>({
    queryKey: ['variant-explorer', 'small-variants', requestQueryString],
    enabled: Boolean(assemblyId),
    queryFn: async () => {
      const res = await api.get(apiPath`/variant-explorer/small-variants?${raw(requestQueryString)}`);
      return res.data as GlobalVariantPage;
    },
    // Keep the current page on screen while the next page/filter loads (the `isFetching`
    // indicator signals the refresh) instead of flashing the empty loading state.
    placeholderData: keepPreviousData,
  });

  const tags = tagDefinitions ?? [];
  const variants = variantPage?.variants ?? [];
  const total = variantPage?.total ?? 0;
  const nextCursor = variantPage?.next_cursor ?? null;

  return (
    <div className="page-shell analysis-shell">
      <section className="surface-card page-top-card">
        <div className="space-y-1">
          <p className="page-kicker">Variants</p>
          <h1 className="catalog-card-title">Variant Explorer</h1>
          <p className="page-copy">
            Aggregate SNVs and small indels across every project you can access. Each row is a
            unique variant with carrier counts summarised across the whole cohort.
          </p>
        </div>
        <div className="variant-explorer-controls">
          <div className="variant-explorer-assembly-row">
            <label className="variant-explorer-assembly-label" htmlFor="variant-explorer-assembly">
              Assembly
            </label>
            <select
              id="variant-explorer-assembly"
              value={assemblyId ?? ''}
              onChange={(event) => setAssemblyId(event.target.value || undefined)}
            >
              {assemblies && assemblies.length > 0 ? (
                assemblies.map((assembly) => (
                  <option key={assembly.assembly_id} value={assembly.assembly_id}>
                    {assembly.assembly_name}
                    {assembly.version ? ` (${assembly.version})` : ''} · {assembly.project_count}{' '}
                    project{assembly.project_count === 1 ? '' : 's'}
                  </option>
                ))
              ) : (
                <option value="">No accessible assemblies</option>
              )}
            </select>
          </div>
        </div>
      </section>

      <section className="surface-card variant-explorer-genotype-card">
        <div className="variant-explorer-genotype-header">
          <span className="variant-explorer-genotype-title">Sample - Genotype Filter</span>
          <label className="variant-explorer-toggle variant-explorer-genotype-imputed">
            <input
              type="checkbox"
              checked={includeImputed}
              onChange={(event) => setIncludeImputed(event.target.checked)}
            />
            Include imputed variants (GLIMPSE2 / SHAPEIT)
          </label>
        </div>

        {genotypeRows.length > 0 ? (
          <ul className="variant-explorer-genotype-rows">
            {genotypeRows.map((row) => (
              <li key={row.sample} className="variant-explorer-genotype-row">
                <span className="variant-explorer-genotype-sample">{row.sample}</span>
                <select
                  aria-label={`Genotype for ${row.sample}`}
                  value={row.mode}
                  onChange={(event) =>
                    updateGenotypeRowMode(row.sample, event.target.value as GenotypeFilterMode)
                  }
                >
                  {(Object.keys(GENOTYPE_MODE_LABELS) as GenotypeFilterMode[]).map((mode) => (
                    <option key={mode} value={mode}>
                      {GENOTYPE_MODE_LABELS[mode]}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="variant-explorer-genotype-remove"
                  onClick={() => removeGenotypeRow(row.sample)}
                  aria-label={`Remove ${row.sample}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        ) : null}

        <div className="variant-explorer-genotype-footer">
          <form
            className="variant-explorer-genotype-add"
            onSubmit={(event) => {
              event.preventDefault();
              addGenotypeRow();
            }}
          >
            <input
              type="text"
              list="variant-explorer-sample-options"
              placeholder="add sample…"
              value={newSample}
              onChange={(event) => setNewSample(event.target.value)}
            />
            <datalist id="variant-explorer-sample-options">
              {(sampleOptions ?? []).map((sample) => (
                <option key={sample} value={sample} />
              ))}
            </datalist>
            <button
              type="submit"
              className="button-secondary variant-explorer-mini-button"
              disabled={!newSample.trim()}
            >
              Add
            </button>
          </form>

          <button
            type="button"
            className="button-secondary variant-explorer-mini-button"
            disabled={!genotypeFilterDirty}
            onClick={() => applyGenotypeFilters(genotypeRows)}
          >
            Apply
          </button>
          {genotypeRows.length > 0 || genotypeFilters.length > 0 ? (
            <button
              type="button"
              className="button-secondary variant-explorer-mini-button"
              onClick={() => {
                setGenotypeRows([]);
                applyGenotypeFilters([]);
              }}
            >
              Clear
            </button>
          ) : null}
        </div>
      </section>

      <section className="surface-card space-y-4">
        <SmallVariantFilterForm
          familyAware={false}
          activeFilterChips={search.activeFilterChips}
          applyPreset={search.applyPreset}
          applySavedPreset={search.applySavedPreset}
          draftFilters={search.draftFilters}
          handleApply={search.handleApply}
          handleGtToggle={search.handleGtToggle}
          handleReset={search.handleReset}
          handleSampleFieldChange={search.handleSampleFieldChange}
          members={search.members}
          relationships={search.relationships}
          removeActiveFilterChip={search.removeActiveFilterChip}
          sampleDraftFilters={search.sampleDraftFilters}
          setDraftFilterValue={search.setDraftFilterValue}
          toggleDraftFilterListValue={search.toggleDraftFilterListValue}
          panels={panels}
          presets={EMPTY_PRESETS}
          tags={tags}
          onSaveCurrentPreset={noopSavePreset}
        />
      </section>

      <section className="surface-card space-y-4">
        <div className="variant-explorer-results-header">
          <p className="analysis-section-title">
            {total.toLocaleString()} variant{total === 1 ? '' : 's'}
            {isFetching ? ' · updating…' : ''}
          </p>
          <button
            type="button"
            className="button-secondary variant-explorer-download"
            onClick={handleDownloadCsv}
            disabled={isExporting || total === 0}
            title="Download all filtered variants with annotation data as CSV"
          >
            {isExporting ? 'Preparing…' : 'Download CSV'}
          </button>
        </div>

        {exportError ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--error">
            {exportError}
          </div>
        ) : null}

        {isLoading ? (
          <p className="table-subtle">Loading variants…</p>
        ) : isError ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--error">
            Failed to load variants.
          </div>
        ) : variants.length === 0 ? (
          <p className="table-subtle">
            No variants match the current filters in your accessible projects.
          </p>
        ) : (
          <>
            <GlobalSmallVariantTable
              variants={variants}
              tagDefinitions={tags}
              sort={sort}
              order={order}
              onSort={setSort}
              onOpenCarriers={(variant, mode) => setCarrierModal({ variant, mode })}
            />
            <div className="variant-explorer-pagination">
              <button
                type="button"
                className="button-secondary"
                disabled={pageNumber <= 1}
                onClick={goToPreviousPage}
              >
                Previous
              </button>
              <span className="table-subtle">Page {pageNumber}</span>
              <button
                type="button"
                className="button-secondary"
                // Also disabled while refetching: the visible next_cursor belongs to the
                // previous result, so advancing mid-fetch (e.g. after a filter change) could
                // page from a stale boundary.
                disabled={!nextCursor || isFetching}
                onClick={() => nextCursor && goToNextPage(nextCursor)}
              >
                Next
              </button>
            </div>
          </>
        )}
      </section>

      {carrierModal ? (
        <VariantCarrierModal
          variant={carrierModal.variant}
          mode={carrierModal.mode}
          assemblyId={assemblyId}
          includeImputed={includeImputed}
          onClose={() => setCarrierModal(null)}
        />
      ) : null}
    </div>
  );
};

export default GlobalSmallVariantExplorerPage;
