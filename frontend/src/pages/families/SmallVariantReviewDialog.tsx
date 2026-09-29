import { useEffect, useState } from 'react';
import { useModalDialog } from '../../lib/useModalDialog';
import {
  ACMG_CLASSIFICATION_TAGS,
  getClassificationLabelFromTagKey,
  getClassificationTagKeyFromClassification,
  getClassificationTagKeyFromTags,
  normalizeTagKeys,
  sortTagDefinitions,
  type SmallVariantPriority,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { formatLocus, SEGREGATION_MODE_LABELS } from './smallVariantResultUtils';

type ReviewableVariant = {
  _id: string;
  chr: string;
  start: number;
  end: number;
  type: string;
  gene?: string;
  gene_id?: string;
  hgvsp?: string;
  hgvsc?: string;
  effect?: string;
  alpha_missense_class?: string | null;
  alpha_missense_pathogenicity?: number;
  gene_pli?: number;
  gene_missense_z?: number;
  review?: SmallVariantReview | null;
  priority?: SmallVariantPriority | null;
};

type SmallVariantReviewDialogProps = {
  familyId?: string;
  members: { sample_id: string }[];
  projectId?: string;
  variant: ReviewableVariant;
  tags: SmallVariantTagDefinition[];
  onClose: () => void;
  onSave: (payload: SmallVariantReviewSavePayload) => Promise<void>;
  isPending?: boolean;
  errorMessage?: string | null;
};

function PriorityBreakdown({
  priority,
  variant,
}: {
  priority: SmallVariantPriority;
  variant: ReviewableVariant;
}) {
  const modes = priority.segregation_modes
    .map((mode) => SEGREGATION_MODE_LABELS[mode] || mode)
    .join(', ');
  const constraintBits = [
    variant.alpha_missense_class
      ? `AlphaMissense ${variant.alpha_missense_class.replace(/_/g, ' ')}${
          typeof variant.alpha_missense_pathogenicity === 'number'
            ? ` (${variant.alpha_missense_pathogenicity.toFixed(2)})`
            : ''
        }`
      : null,
    typeof variant.gene_pli === 'number' ? `gene pLI ${variant.gene_pli.toFixed(2)}` : null,
    typeof variant.gene_missense_z === 'number'
      ? `missense Z ${variant.gene_missense_z.toFixed(2)}`
      : null,
  ].filter(Boolean);
  return (
    <section className="variant-review-modal-section">
      <div className="variant-review-note-header">
        <p className="analysis-section-title">
          Priority score {priority.combined_score.toFixed(2)}
          {priority.rank ? ` · rank ${priority.rank}` : ''}
        </p>
        <p className="table-subtle">
          Exomiser-style: variant impact + rarity + segregation, ranked by gene–phenotype
          match.
        </p>
      </div>
      <div className="variant-priority-grid">
        <div>
          <span className="variant-priority-label">Variant</span>
          <span className="variant-priority-value">{priority.variant_score.toFixed(2)}</span>
        </div>
        <div>
          <span className="variant-priority-label">Pathogenicity</span>
          <span className="variant-priority-value">
            {priority.pathogenicity_score.toFixed(2)}
          </span>
        </div>
        <div>
          <span className="variant-priority-label">Rarity</span>
          <span className="variant-priority-value">{priority.frequency_score.toFixed(2)}</span>
        </div>
        <div>
          <span className="variant-priority-label">Phenotype</span>
          <span className="variant-priority-value">
            {typeof priority.phenotype_score === 'number'
              ? priority.phenotype_score.toFixed(2)
              : 'n/a'}
          </span>
        </div>
      </div>
      {modes ? (
        <p className="table-subtle">Compatible inheritance: {modes}</p>
      ) : priority.segregation_weight >= 1 ? (
        <p className="table-subtle">
          Segregation not evaluated (no affected individuals flagged).
        </p>
      ) : (
        <p className="table-subtle">No compatible inheritance mode for the pedigree.</p>
      )}
      {constraintBits.length ? (
        <p className="table-subtle">{constraintBits.join(' · ')}</p>
      ) : null}
      {priority.phenotype_matches.length ? (
        <p className="table-subtle">
          Phenotype match{priority.phenotype_gene ? ` (${priority.phenotype_gene})` : ''}:{' '}
          {priority.phenotype_matches.map((m) => m.label || m.hpo_id).join(', ')}
        </p>
      ) : typeof priority.phenotype_score !== 'number' ? (
        <p className="table-subtle">No Monarch phenotype data for this gene.</p>
      ) : null}
    </section>
  );
}

export default function SmallVariantReviewDialog({
  variant,
  tags,
  onClose,
  onSave,
  isPending = false,
  errorMessage = null,
}: SmallVariantReviewDialogProps) {
  const [classificationTagKey, setClassificationTagKey] = useState('');
  // The class shown when the dialog opened, to tell a classification left alone from one
  // the user changed.
  const [shownClassificationTagKey, setShownClassificationTagKey] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [note, setNote] = useState('');

  useEffect(() => {
    const variantClassificationTagKey =
      getClassificationTagKeyFromTags(variant.review?.tags || []) ||
      getClassificationTagKeyFromClassification(variant.review?.classification);
    setClassificationTagKey(variantClassificationTagKey);
    setShownClassificationTagKey(variantClassificationTagKey);
    setSelectedTags(
      normalizeTagKeys((variant.review?.tags || []).filter((tag) => tag !== variantClassificationTagKey)),
    );
    setNote(variant.review?.note || '');
  }, [variant]);

  const classificationOptions = ACMG_CLASSIFICATION_TAGS.map((option) => ({
    value: option.key,
    label: option.label,
  }));
  const sortedTagDefinitions = sortTagDefinitions(tags);
  const standardTagOptions = sortedTagDefinitions
    .filter((tag) => !ACMG_CLASSIFICATION_TAGS.some((option) => option.key === tag.key))
    .filter((tag) => !tag.is_custom)
    .map((tag) => ({
      value: tag.key,
      label: tag.label,
    }));
  const customTagOptions = sortedTagDefinitions
    .filter((tag) => !ACMG_CLASSIFICATION_TAGS.some((option) => option.key === tag.key))
    .filter((tag) => tag.is_custom)
    .map((tag) => ({
      value: tag.key,
      label: tag.label,
    }));

  const toggleTag = (
    key: string,
    selected: string[],
    setSelected: (tags: string[]) => void,
  ) => {
    const next = new Set(selected);
    if (next.has(key)) {
      next.delete(key);
    } else {
      next.add(key);
    }
    setSelected(normalizeTagKeys(next));
  };

  const handleSave = async () => {
    try {
      const combinedTags = normalizeTagKeys(
        [...selectedTags, classificationTagKey].filter(Boolean),
      );
      const chosenLabel = getClassificationLabelFromTagKey(classificationTagKey);
      const payload: SmallVariantReviewSavePayload = {
        classification:
          chosenLabel ||
          (classificationTagKey === shownClassificationTagKey
            ? // Left alone: a stored label the class boxes cannot show (the ClinGen CNV
              // dialog writes one without a class tag) goes back unchanged. Left out, the
              // save would erase it.
              variant.review?.classification || undefined
            : // The user unticked the class shown: clear it, explicitly.
              null),
        tags: combinedTags,
        note: note.trim() || undefined,
      };
      await onSave(payload);
    } catch {
      // Parent keeps the dialog open and surfaces the error.
    }
  };

  // Escape, focus trap, and a check before unsaved input is discarded (#529).
  const dialog = useModalDialog({
    onClose,
    discardMessage: 'Discard your unsaved changes to this review?',
  });

  return (
    <div className="modal-backdrop" role="presentation" {...dialog.backdropProps}>
      <div
        className="modal-surface surface-card variant-review-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="small-variant-review-title"
        ref={dialog.dialogRef}
        tabIndex={-1}
        {...dialog.surfaceProps}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">Variant Review</p>
            <h2 id="small-variant-review-title" className="catalog-card-title">
              {variant.gene || variant.gene_id || 'Intergenic variant'}
            </h2>
            <p className="variant-review-modal-subtitle">
              {formatLocus(variant)} · {variant.hgvsp || variant.hgvsc || variant.effect || variant.type}
            </p>
          </div>
          <button type="button" className="button-secondary" onClick={dialog.requestClose}>
            Close
          </button>
        </div>

        <div className="variant-review-modal-body">
          {errorMessage ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">
              {errorMessage}
            </div>
          ) : null}

          {variant.priority ? (
            <PriorityBreakdown priority={variant.priority} variant={variant} />
          ) : null}

          <section className="variant-review-modal-section">
            <div className="variant-review-note-header">
              <p className="analysis-section-title">Variant-level review</p>
              <p className="table-subtle">Tags and note.</p>
            </div>
            <div className="variant-review-curation-columns">
              <div className="variant-review-tag-column">
                <p className="variant-annotation-impact-title">Classification</p>
                <div className="variant-review-tag-list">
                  {classificationOptions.map((option) => (
                    <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={classificationTagKey === option.value}
                        onChange={() =>
                          setClassificationTagKey((current) =>
                            current === option.value ? '' : option.value,
                          )
                        }
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </div>
              <div className="variant-review-tag-column">
                <p className="variant-annotation-impact-title">Standard tags</p>
                <div className="variant-review-tag-list">
                  {standardTagOptions.map((option) => (
                    <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={selectedTags.includes(option.value)}
                        onChange={() => toggleTag(option.value, selectedTags, setSelectedTags)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </div>
              <div className="variant-review-tag-column">
                <p className="variant-annotation-impact-title">Custom tags</p>
                {customTagOptions.length ? (
                  <div className="variant-review-tag-list">
                    {customTagOptions.map((option) => (
                      <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                        <input
                          type="checkbox"
                          checked={selectedTags.includes(option.value)}
                          onChange={() => toggleTag(option.value, selectedTags, setSelectedTags)}
                        />
                        {option.label}
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="table-subtle">No custom tags available.</p>
                )}
              </div>
            </div>
            <textarea
              className="variant-review-textarea"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              placeholder="Note or rationale"
            />
          </section>
        </div>

        <div className="variant-search-actions variant-review-modal-actions">
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setClassificationTagKey('');
              setSelectedTags([]);
              setNote('');
            }}
          >
            Clear
          </button>
          <button
            type="button"
            className="form-button"
            onClick={() => {
              void handleSave();
            }}
            disabled={isPending}
          >
            {isPending ? 'Saving…' : 'Save review'}
          </button>
        </div>
      </div>
    </div>
  );
}
