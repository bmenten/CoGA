import { useEffect, useState } from 'react';
import {
  ACMG_CLASSIFICATION_TAGS,
  getClassificationLabelFromTagKey,
  getClassificationTagKeyFromClassification,
  getClassificationTagKeyFromTags,
  normalizeTagKeys,
  sortTagDefinitions,
  type SmallVariantReview,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { formatLocus } from './smallVariantResultUtils';

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
  review?: SmallVariantReview | null;
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

export default function SmallVariantReviewDialog({
  variant,
  tags,
  onClose,
  onSave,
  isPending = false,
  errorMessage = null,
}: SmallVariantReviewDialogProps) {
  const [classificationTagKey, setClassificationTagKey] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [note, setNote] = useState('');

  useEffect(() => {
    const variantClassificationTagKey =
      getClassificationTagKeyFromTags(variant.review?.tags || []) ||
      getClassificationTagKeyFromClassification(variant.review?.classification);
    setClassificationTagKey(variantClassificationTagKey);
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
      const payload: SmallVariantReviewSavePayload = {
        classification: getClassificationLabelFromTagKey(classificationTagKey) || undefined,
        tags: combinedTags,
        note: note.trim() || undefined,
      };
      await onSave(payload);
    } catch {
      // Parent keeps the dialog open and surfaces the error.
    }
  };

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-surface surface-card variant-review-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="small-variant-review-title"
        onClick={(event) => event.stopPropagation()}
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
          <button type="button" className="button-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="variant-review-modal-body">
          {errorMessage ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">
              {errorMessage}
            </div>
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
