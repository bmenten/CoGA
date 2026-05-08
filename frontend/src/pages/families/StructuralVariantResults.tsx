import { useMemo, useState } from 'react';
import ResultsPagination from './ResultsPagination';
import StructuralVariantCards from './StructuralVariantCards';
import StructuralVariantColumnControls from './StructuralVariantColumnControls';
import StructuralVariantSummaryTable from './StructuralVariantSummaryTable';
import StructuralVariantTable from './StructuralVariantTable';
import SmallVariantReviewDialog from './SmallVariantReviewDialog';
import {
  CARD_VIEW_THRESHOLD,
  sortStructuralVariants,
  type StructuralSortableKeys,
  type StructuralSummary,
  type StructuralVariant,
  type StructuralVariantFamilyMember,
  type StructuralVariantReviewSavePayload,
  type StructuralVariantTagDefinition,
} from './structuralVariantSearch';

type ResultViewMode = 'auto' | 'table' | 'cards';

type StructuralVariantResultsProps = {
  familyId?: string;
  filteredTotal: number;
  linkSearch: string;
  members: StructuralVariantFamilyMember[];
  onPageChange: (nextPage: number) => void;
  overallTotal: number;
  page: number;
  projectId?: string;
  reviewError?: string | null;
  reviewIsPending?: boolean;
  summary: StructuralSummary;
  tags: StructuralVariantTagDefinition[];
  totalPages: number;
  variants: StructuralVariant[];
  onOpenReview?: () => void;
  onSaveReview?: (variant: StructuralVariant, payload: StructuralVariantReviewSavePayload) => Promise<void>;
  onToggleReviewTag?: (variant: StructuralVariant, tagKey: string) => Promise<void>;
};

export default function StructuralVariantResults({
  familyId,
  filteredTotal,
  linkSearch,
  members,
  onPageChange,
  overallTotal,
  page,
  projectId,
  reviewError = null,
  reviewIsPending = false,
  summary,
  tags,
  totalPages,
  variants,
  onOpenReview,
  onSaveReview,
  onToggleReviewTag,
}: StructuralVariantResultsProps) {
  const [viewMode, setViewMode] = useState<ResultViewMode>('auto');
  const [selectedVariant, setSelectedVariant] = useState<StructuralVariant | null>(null);
  const [visible, setVisible] = useState({
    chr: true,
    start: true,
    end: true,
    length: true,
    type: true,
    source: false,
    gene: true,
    cytoband: true,
    inheritance: true,
    control_af: true,
    phenotype: true,
    region_flags: false,
    qual: false,
    read_support: false,
    filter: false,
    remote_chr: false,
    remote_start: false,
    genotypes: true,
  });
  const [sortKey, setSortKey] = useState<StructuralSortableKeys>('chr');
  const [sortAsc, setSortAsc] = useState(true);

  const sortedVariants = useMemo(
    () => sortStructuralVariants(variants, sortKey, sortAsc),
    [sortAsc, sortKey, variants],
  );
  const resolvedViewMode =
    viewMode === 'auto' && variants.length <= CARD_VIEW_THRESHOLD
      ? 'cards'
      : viewMode === 'auto'
        ? 'table'
        : viewMode;

  const handleSort = (key: StructuralSortableKeys) => {
    if (sortKey === key) {
      setSortAsc((value) => !value);
    } else {
      setSortKey(key);
      setSortAsc(true);
    }
  };

  const toggleColumn = (key: string) =>
    setVisible((current) => ({ ...current, [key]: !current[key as keyof typeof current] }));

  return (
    <>
      <section className="surface-card space-y-4">
        <div className="variant-results-toolbar">
          <div className="space-y-1">
            <h2 className="section-title">Variants</h2>
            <p className="table-subtle">
              Filtered {filteredTotal.toLocaleString()} of {overallTotal.toLocaleString()} imported
              SVs. Auto view switches to cards at {CARD_VIEW_THRESHOLD} results.
            </p>
          </div>
          <div className="variant-results-toggle" role="tablist" aria-label="SV display mode">
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

        <StructuralVariantSummaryTable summary={summary} />

        {resolvedViewMode === 'cards' ? (
          <StructuralVariantCards
            familyId={familyId}
            linkSearch={linkSearch}
            members={members}
            onEditReview={(variant) => {
              onOpenReview?.();
              setSelectedVariant(variant);
            }}
            onToggleReviewTag={onToggleReviewTag}
            projectId={projectId}
            reviewIsPending={reviewIsPending}
            tags={tags}
            variants={sortedVariants}
          />
        ) : (
          <>
            <StructuralVariantColumnControls visible={visible} onToggleColumn={toggleColumn} />
            <StructuralVariantTable
              familyId={familyId}
              linkSearch={linkSearch}
              members={members}
              onEditReview={(variant) => {
                onOpenReview?.();
                setSelectedVariant(variant);
              }}
              projectId={projectId}
              reviewIsPending={reviewIsPending}
              onToggleReviewTag={onToggleReviewTag}
              tags={tags}
              variants={sortedVariants}
              sortKey={sortKey}
              sortAsc={sortAsc}
              visible={visible}
              onSort={handleSort}
            />
          </>
        )}
      </section>

      <ResultsPagination page={page} totalPages={totalPages} onPageChange={onPageChange} />
      {selectedVariant && onSaveReview ? (
        <SmallVariantReviewDialog
          familyId={familyId}
          members={members}
          projectId={projectId}
          variant={selectedVariant}
          tags={tags}
          isPending={reviewIsPending}
          errorMessage={reviewError}
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
