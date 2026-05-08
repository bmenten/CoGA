import { useState } from 'react';
import {
  CARD_VIEW_THRESHOLD,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantPage,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import ResultsPagination from './ResultsPagination';
import SmallVariantCards from './SmallVariantCards';
import SmallVariantPairCards from './SmallVariantPairCards';
import SmallVariantReviewDialog from './SmallVariantReviewDialog';
import SmallVariantTable from './SmallVariantTable';

type ResultViewMode = 'auto' | 'table' | 'cards';

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
  speciesName?: string;
  totalPages: number;
  tags: SmallVariantTagDefinition[];
  onToggleReviewTag: (variant: SmallVariant, tagKey: string) => Promise<void>;
  onSaveReview: (variant: SmallVariant, payload: SmallVariantReviewSavePayload) => Promise<void>;
  onOpenReview?: () => void;
  reviewIsPending?: boolean;
  reviewError?: string | null;
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
  speciesName,
  totalPages,
  tags,
  onToggleReviewTag,
  onSaveReview,
  onOpenReview,
  reviewIsPending = false,
  reviewError = null,
}: SmallVariantResultsProps) {
  const [viewMode, setViewMode] = useState<ResultViewMode>('auto');
  const [selectedVariant, setSelectedVariant] = useState<SmallVariant | null>(null);
  const pairGroups = data?.variant_groups || [];
  const hasFlatVariants = Boolean(data?.variants.length);
  const hasGroupedPairs = Boolean(pairGroups.length);
  const hasResults = hasFlatVariants || hasGroupedPairs;

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
            <h2 className="section-title">Variants</h2>
            <p className="table-subtle">
              Auto view switches to cards at {CARD_VIEW_THRESHOLD} results. Compound-het matches stay grouped by pair.
            </p>
          </div>
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
    </>
  );
}
