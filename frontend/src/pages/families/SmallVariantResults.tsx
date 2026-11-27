import { useState } from 'react';
import api from '../../lib/api';
import { describeCsvExport, saveCsvBlob, truncatedExportMessage } from '../../lib/csvExport';
import {
  CARD_VIEW_THRESHOLD,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantPage,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import AcmgClassificationModal from './AcmgClassificationModal';
import CandidateCapNotice from './CandidateCapNotice';
import ResultsPagination from './ResultsPagination';
import SmallVariantCards, { type SmallVariantCardListProps } from './SmallVariantCards';
import SmallVariantGroupCards from './SmallVariantGroupCards';
import SmallVariantReviewDialog from './SmallVariantReviewDialog';
import SmallVariantTable from './SmallVariantTable';
import { RankedByNote } from './RankByField';
import { formatVariantTotal } from './smallVariantResultUtils';
import { GNOMAD_COUNT_NOTE_DETAIL, gnomadCountNote } from './gnomadCountNotes';
import { apiPath, raw } from '../../lib/apiPath';
import type { ApiFamilyRelationship } from '../../lib/apiTypes';
import { FamilyReadOnlyContext } from '../../lib/familyAccess';

type ResultViewMode = 'table' | 'cards';

export interface CsvExportTarget {
  /** The export's API path, with the list's query string. */
  path: string;
  /** The saved file's name before `.csv`, marked when the export was cut. */
  filenameStem: string;
}

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

// The count beside the Results heading. A compound-het or carrier gene is one result, so where the
// variants are not the results it counts the genes and the variants, as the top of the page does.
const describeResultsCount = (data: SmallVariantPage): string => {
  const format = (count: number) => formatVariantTotal(count, data.total_is_estimated, data.count_limit);
  const results = format(data.total);
  if (typeof data.variant_total !== 'number' || typeof data.gene_total !== 'number') return results;
  const variants = format(data.variant_total);
  if (variants === results) return results;
  const genes = format(data.gene_total);
  return `${genes} gene${genes === '1' ? '' : 's'} · ${variants} variant${variants === '1' ? '' : 's'}`;
};

type SmallVariantResultsProps = {
  assemblyName?: string;
  assemblyVersion?: string;
  data?: SmallVariantPage;
  familyId?: string;
  locationSearch: string;
  members: FamilyMember[];
  // The family's pedigree links, for the ACMG de novo criteria (PS2/PM6).
  relationships?: ApiFamilyRelationship[];
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
  // What Download CSV downloads: the export endpoint with the list's query, and the stem of the
  // saved file's name. Without one, the family's small-variant export of `requestQueryString`;
  // the NIPT page names its own.
  csvExport?: CsvExportTarget;
  // The caller is a reader in the family's projects: the review controls inside are
  // disabled, and the reviews stay readable.
  readOnly?: boolean;
};

export default function SmallVariantResults({
  assemblyName,
  assemblyVersion,
  data,
  familyId,
  locationSearch,
  members,
  relationships,
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
  csvExport,
  readOnly = false,
}: SmallVariantResultsProps) {
  // Null until the user picks a view: a short list shows as cards, a longer one as a table.
  const [viewMode, setViewMode] = useState<ResultViewMode | null>(null);
  const [selectedVariant, setSelectedVariant] = useState<SmallVariant | null>(null);
  const [acmgVariant, setAcmgVariant] = useState<SmallVariant | null>(null);
  const [isExporting, setIsExporting] = useState(false);
  const [exportError, setExportError] = useState<string | null>(null);
  const [exportWarning, setExportWarning] = useState<string | null>(null);
  const resultGroups = data?.variant_groups || [];
  const compoundHetGenes = resultGroups.filter((group) => group.group_type === 'compound_het');
  const carrierGenes = resultGroups.filter((group) => group.group_type === 'carrier_gene');
  const hasFlatVariants = Boolean(data?.variants.length);
  const hasCompoundHetGenes = Boolean(compoundHetGenes.length);
  const hasCarrierGenes = Boolean(carrierGenes.length);
  const hasResults = hasFlatVariants || hasCompoundHetGenes || hasCarrierGenes;
  // A maximum gnomAD count filter cannot exclude a variant without that count.
  const countNote = gnomadCountNote(requestQueryString, data);
  const resultsCount = data ? describeResultsCount(data) : null;

  const handleDownloadCsv = async () => {
    const target =
      csvExport ??
      (familyId
        ? {
            path: apiPath`/families/${familyId}/small-variants/export?${raw(requestQueryString)}`,
            filenameStem: `family-${familyId}-small-variants`,
          }
        : null);
    if (!target) {
      return;
    }
    setExportError(null);
    setExportWarning(null);
    setIsExporting(true);
    try {
      const res = await api.get(target.path, { responseType: 'blob' });
      const info = describeCsvExport(res, target.filenameStem);
      saveCsvBlob(res.data, info.filename);
      if (info.truncated) setExportWarning(truncatedExportMessage(info));
    } catch {
      setExportError('Could not export variants. Try narrowing your filters and retry.');
    } finally {
      setIsExporting(false);
    }
  };

  const resolvedViewMode: ResultViewMode =
    viewMode ?? ((data?.total ?? 0) <= CARD_VIEW_THRESHOLD ? 'cards' : 'table');

  // What every card and row takes, the single variants' and the groups' alike.
  const cardProps: SmallVariantCardListProps = {
    members,
    familyId,
    projectId,
    locationSearch,
    speciesName,
    assemblyName,
    assemblyVersion,
    tags,
    reviewIsPending,
    onEditReview: (variant) => {
      onOpenReview?.();
      setSelectedVariant(variant);
    },
    onAcmgClassify: (variant) => {
      onOpenReview?.();
      setAcmgVariant(variant);
    },
    onToggleReviewTag,
    rankedBy: data?.ranked_by,
    niptTrio: data?.trio,
  };

  return (
    <FamilyReadOnlyContext.Provider value={readOnly}>
      <section className="surface-card space-y-4">
        <div className="variant-results-toolbar">
          <div className="space-y-1">
            <h2 className="section-title">
              Results
              {resultsCount ? <span className="variant-results-count">{resultsCount}</span> : null}
            </h2>
            <p className="table-subtle">
              Up to {CARD_VIEW_THRESHOLD} results show as cards, more as a paginated table, until
              you pick a view. Compound-het matches and a carrier screen&rsquo;s stay grouped by gene;
              a gene counts as one result.
            </p>
          </div>
          <div className="variant-results-toolbar-actions">
            <button
              type="button"
              className="button-secondary variant-explorer-download"
              onClick={handleDownloadCsv}
              disabled={isExporting || !hasResults}
              title="Download all filtered variants with annotation data as CSV"
            >
              {isExporting ? 'Preparing…' : 'Download CSV'}
            </button>
            <div className="variant-results-toggle" role="tablist" aria-label="Variant display mode">
              {[
                { value: 'table', label: 'Table' },
                { value: 'cards', label: 'Cards' },
              ].map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="tab"
                  aria-selected={resolvedViewMode === option.value}
                  className={`pill-toggle ${resolvedViewMode === option.value ? 'pill-toggle--active' : ''}`}
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

        {exportWarning ? (
          <div className="variant-workspace-feedback variant-workspace-feedback--warning" role="alert">
            {exportWarning}
          </div>
        ) : null}

        <CandidateCapNotice page={data} noun="variants" />

        {countNote ? (
          <div
            className="variant-workspace-feedback variant-workspace-feedback--warning"
            role="note"
            title={GNOMAD_COUNT_NOTE_DETAIL}
          >
            {countNote}
          </div>
        ) : null}

        {data?.ranking_cached ? (
          <p className="ranking-cache-note" title="The ranking refreshes automatically when phenotypes, the pedigree, the gene panel, or annotations change.">
            <span aria-hidden>⚡</span> Prioritised ranking served from cache
            {data.ranking_computed_at ? ` · computed ${formatRelativeTime(data.ranking_computed_at)}` : ''}.
          </p>
        ) : null}

        <RankedByNote rankedBy={data?.ranked_by} />

        {!hasResults ? (
          <div className="variant-results-empty">
            <p className="table-empty">No variants match the current search.</p>
          </div>
        ) : (
          <div className="space-y-6">
            {hasCompoundHetGenes ? (
              <div className="space-y-3">
                <div className="space-y-1">
                  <h3 className="section-title">Compound-het genes</h3>
                  <p className="table-subtle">
                    Each gene with two or more variants that can be the recessive cause together,
                    by the parent each came from, with the pairs among them.
                  </p>
                </div>
                <SmallVariantGroupCards groups={compoundHetGenes} memberView={resolvedViewMode} {...cardProps} />
              </div>
            ) : null}

            {hasCarrierGenes ? (
              <div className="space-y-3">
                <div className="space-y-1">
                  <h3 className="section-title">Carrier genes</h3>
                  <p className="table-subtle">
                    Each gene the couple is at risk in, with every variant either partner carries
                    there.
                  </p>
                </div>
                <SmallVariantGroupCards groups={carrierGenes} memberView={resolvedViewMode} {...cardProps} />
              </div>
            ) : null}

            {hasFlatVariants ? (
              <div className="space-y-3">
                {hasCompoundHetGenes || hasCarrierGenes ? (
                  <div className="space-y-1">
                    <h3 className="section-title">Single variants</h3>
                    <p className="table-subtle">
                      Single-variant results that remain relevant under the active filter.
                    </p>
                  </div>
                ) : null}
                {resolvedViewMode === 'cards' ? (
                  <SmallVariantCards variants={data?.variants || []} {...cardProps} />
                ) : (
                  <SmallVariantTable variants={data?.variants || []} {...cardProps} />
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
          relationships={relationships}
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
    </FamilyReadOnlyContext.Provider>
  );
}
