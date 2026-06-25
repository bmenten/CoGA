import { useState } from 'react';
import api from '../../lib/api';
import {
  CARD_VIEW_THRESHOLD,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantPage,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import AcmgClassificationModal from './AcmgClassificationModal';
import ResultsPagination from './ResultsPagination';
import SmallVariantCards from './SmallVariantCards';
import SmallVariantPairCards from './SmallVariantPairCards';
import SmallVariantReviewDialog from './SmallVariantReviewDialog';
import SmallVariantTable from './SmallVariantTable';

type ResultViewMode = 'auto' | 'table' | 'cards';

// Compact "x ago" for the prioritised-ranking cache indicator.
const formatRelativeTime = (iso: string): string => {
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '';
  const seconds = Math.max(0, Math.round((Date.now() - then) / 1000));
  if (seconds < 60) return 'just now';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} h ago`;
  return `${Math.round(hours / 24)} d ago`;
};

type SmallVariantResultsProps = {
  assemblyName?: string;
  assemblyVersion?: string;
  data?: SmallVariantPage;
  familyId?: string;
  locationSearch: string;
  members: FamilyMember[];
  onPageChange: (nextPage: number) => void;
  page: number;
  projectId?: string;
  requestQueryString: string;
  speciesName?: string;
  totalPages: number;
  tags: SmallVariantTagDefinition[];
  onToggleReviewTag: (variant: SmallVariant, tagKey: string) => Promise<void>;
  onSaveReview: (variant: SmallVariant, payload: SmallVariantReviewSavePayload) => Promise<void>;
  onOpenReview?: () => void;
  reviewIsPending?: boolean;
  reviewError?: string | null;
  // The CSV export hits the small-variant export endpoint; callers without one
  // (e.g. the NIPT page) hide the button by passing false.
  showCsvExport?: boolean;
};

export default function SmallVariantResults({
  assemblyName,
  assemblyVersion,
  data,
  familyId,
  locationSearch,
  members,
  onPageChange,
  page,
  projectId,
  requestQueryString,
  speciesName,
  totalPages,
  tags,
  onToggleReviewTag,
  onSaveReview,
  onOpenReview,
  reviewIsPending = false,
  reviewError = null,
  showCsvExport = true,
}: SmallVariantResultsProps) {
  const [viewMode, setViewMode] = useState<ResultViewMode>('auto');
  const [selectedVariant, setSelectedVariant] = useState<SmallVariant | null>(null);
  const [acmgVariant, setAcmgVariant] = useState<SmallVariant | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const pairGroups = data?.variant_groups || [];
  const hasFlatVariants = Boolean(data?.variants.length);
  const hasGroupedPairs = Boolean(pairGroups.length);
  const hasResults = hasFlatVariants || hasGroupedPairs;

  const handleDownloadCsv = async () => {
    if (!familyId) {
      return;
    }
    setExportError(null);
    setIsExporting(true);
    try {
      const res = await api.get(
        `/families/${familyId}/small-variants/export?${requestQueryString}`,
        { responseType: 'blob' },
      );
      const url = window.URL.createObjectURL(new Blob([res.data], { type: 'text/csv' }));
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `family-${familyId}-small-variants.csv`;
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

  const resolvedViewMode =
    viewMode === 'auto' && (data?.total ?? 0) <= CARD_VIEW_THRESHOLD
      ? 'cards'
      : viewMode === 'auto'
        ? 'table'
        : viewMode;

  return (
    <>
      <section className="surface-card space-y-4">
        <div className="variant-results-toolbar">
          <div className="space-y-1">
            <h2 className="section-title">
              Variants
              {typeof data?.total === 'number' ? (
                <span className="variant-results-count">
                  {data.total.toLocaleString()}
                  {data.total_is_estimated ? '+' : ''}
                </span>
              ) : null}
            </h2>
            <p className="table-subtle">
              Auto view shows up to {CARD_VIEW_THRESHOLD} variants as cards and switches to a
              paginated list beyond that. Compound-het matches stay grouped by pair.
            </p>
          </div>
          <div className="variant-results-toolbar-actions">
            {showCsvExport && (
              <button
                type="button"
                className="button-secondary variant-explorer-download"
                onClick={handleDownloadCsv}
                disabled={isExporting || !hasResults}
                title="Download all filtered variants with annotation data as CSV"
              >
                {isExporting ? 'Preparing…' : 'Download CSV'}
              </button>
            )}
            <div className="variant-results-toggle" role="tablist" aria-label="Variant display mode">
              {[
                { value: 'auto', label: 'Auto' },
                { value: 'table', label: 'Table' },
                { value: 'cards', label: 'Cards' },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={viewMode === option.value}
                  className={`pill-toggle ${viewMode === option.value ? 'pill-toggle--active' : ''}`}
                  onClick={() => setViewMode(option.value as ResultViewMode)}
                >
                  {option.label}
                </button>
              ))}
            </div>
          </div>
        </div>

        {exportError ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--error">
            {exportError}
          </div>
        ) : null}

        {data?.ranking_truncated ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--warning">
            More candidates matched than the prioritizer ranks at once, so this ranking is
            incomplete — the top variant may not be shown. Narrow the filters (tighter
            frequency/impact, a gene panel, or an inheritance mode) to rank the full set.
          </div>
        ) : null}

        {data?.ranking_cached ? (
          <p className="ranking-cache-note" title="The ranking refreshes automatically when phenotypes, the pedigree, the gene panel, or annotations change.">
            <span aria-hidden>⚡</span> Prioritised ranking served from cache
            {data.ranking_computed_at ? ` · computed ${formatRelativeTime(data.ranking_computed_at)}` : ''}.
          </p>
        ) : null}

        {!hasResults ? (
          <div className="variant-results-empty">
            <p className="table-empty">No variants match the current search.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {hasGroupedPairs ? (
              <div className="space-y-3">
                <div className="space-y-1">
                  <h3 className="section-title">Compound-Het Pairs</h3>
                  <p className="table-subtle">
                    Pair-level search results aligned to the active inheritance filter.
                  </p>
                </div>
                <SmallVariantPairCards
                  groups={pairGroups}
                  members={members}
                  familyId={familyId}
                  projectId={projectId}
                  locationSearch={locationSearch}
                  tags={tags}
                  onEditReview={setSelectedVariant}
                />
              </div>
            ) : null}

            {hasFlatVariants ? (
              <div className="space-y-3">
                {hasGroupedPairs ? (
                  <div className="space-y-1">
                    <h3 className="section-title">Single Variants</h3>
                    <p className="table-subtle">
                      Single-variant results that remain relevant under the active filter.
                    </p>
                  </div>
                ) : null}
                {resolvedViewMode === 'cards' ? (
                  <SmallVariantCards
                    variants={data?.variants || []}
                    members={members}
                    familyId={familyId}
                    projectId={projectId}
                    locationSearch={locationSearch}
                    speciesName={speciesName}
                    assemblyName={assemblyName}
                    assemblyVersion={assemblyVersion}
                    tags={tags}
                    reviewIsPending={reviewIsPending}
                    onEditReview={(variant) => {
                      onOpenReview?.();
                      setSelectedVariant(variant);
                    }}
                    onAcmgClassify={(variant) => {
                      onOpenReview?.();
                      setAcmgVariant(variant);
                    }}
                    onToggleReviewTag={onToggleReviewTag}
                  />
                ) : (
                  <SmallVariantTable
                    variants={data?.variants || []}
                    members={members}
                    familyId={familyId}
                    projectId={projectId}
                    locationSearch={locationSearch}
                    tags={tags}
                    reviewIsPending={reviewIsPending}
                    onEditReview={(variant) => {
                      onOpenReview?.();
                      setSelectedVariant(variant);
                    }}
                    onAcmgClassify={(variant) => {
                      onOpenReview?.();
                      setAcmgVariant(variant);
                    }}
                    onToggleReviewTag={onToggleReviewTag}
                  />
                )}
              </div>
            ) : null}
          </div>
        )}
      </section>

      <ResultsPagination page={page} totalPages={totalPages} onPageChange={onPageChange} />

      {selectedVariant ? (
        <SmallVariantReviewDialog
          familyId={familyId}
          members={members}
          projectId={projectId}
          variant={selectedVariant}
          tags={tags}
          errorMessage={reviewError}
          isPending={reviewIsPending}
          onClose={() => setSelectedVariant(null)}
          onSave={async (payload) => {
            await onSaveReview(selectedVariant, payload);
            setSelectedVariant(null);
          }}
        />
      ) : null}

      {acmgVariant ? (
        <AcmgClassificationModal
          familyId={familyId}
          projectId={projectId}
          variant={acmgVariant}
          members={members}
          tagDefinitions={tags}
          speciesName={speciesName}
          assemblyName={assemblyName}
          assemblyVersion={assemblyVersion}
          errorMessage={reviewError}
          isPending={reviewIsPending}
          onClose={() => setAcmgVariant(null)}
          onSave={async (payload) => {
            await onSaveReview(acmgVariant, payload);
            setAcmgVariant(null);
          }}
        />
      ) : null}
    </>
  );
}
