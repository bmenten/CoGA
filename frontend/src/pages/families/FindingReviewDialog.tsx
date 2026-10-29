import { useEffect, useState } from 'react';
import { useModalDialog } from '../../lib/useModalDialog';
import ReadOnlyNote from '../../components/ReadOnlyNote';
import { READ_ONLY_HINT, useFamilyReadOnly } from '../../lib/familyAccess';
import {
  addableTagDefinitions,
  heldTagOptions,
  normalizeTagKeys,
  sortTagDefinitions,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import { reviewScale, type FindingKind, type FindingReview, type FindingReviewPayload } from './findingReview';

/**
 * The review of a repeat locus or Paraphase region: a classification on the type's own scale
 * (docs/repeat-paraphase-review-design.md, B4), tags and a note. The ACMG class tags and VUS
 * tiers are not offered (B14): they are the small variants' ACMG classes and would read as a
 * second classification beside this one. One the review already holds is listed, marked, so it
 * can be unticked.
 */
const isAcmgClassTag = (key: string) => key.startsWith('acmg_');

export default function FindingReviewDialog({
  kind,
  title,
  subtitle,
  review,
  tags,
  onClose,
  onSave,
  isPending = false,
  errorMessage = null,
}: {
  kind: FindingKind;
  title: string;
  subtitle?: string;
  review: FindingReview | null | undefined;
  tags: SmallVariantTagDefinition[];
  onClose: () => void;
  /** Resolves true once saved; the dialog then closes. */
  onSave: (payload: FindingReviewPayload) => Promise<boolean>;
  isPending?: boolean;
  errorMessage?: string | null;
}) {
  // A reader in the family's projects sees the review but cannot change it (SEC-1).
  const readOnly = useFamilyReadOnly();
  const [classification, setClassification] = useState('');
  const [selectedTags, setSelectedTags] = useState<string[]>([]);
  const [note, setNote] = useState('');

  useEffect(() => {
    setClassification(review?.classification ?? '');
    setSelectedTags(normalizeTagKeys(review?.tags ?? []));
    setNote(review?.note ?? '');
  }, [review]);

  const offered = sortTagDefinitions(addableTagDefinitions(tags)).filter((tag) => !isAcmgClassTag(tag.key));
  const standardTagOptions = offered.filter((tag) => !tag.is_custom).map((tag) => ({ value: tag.key, label: tag.label }));
  const customTagOptions = offered.filter((tag) => tag.is_custom).map((tag) => ({ value: tag.key, label: tag.label }));
  const held = heldTagOptions(
    review?.tags ?? [],
    tags,
    [...standardTagOptions, ...customTagOptions].map((option) => option.value),
  );
  const toggleTag = (key: string) => {
    const next = new Set(selectedTags);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setSelectedTags(normalizeTagKeys(next));
  };

  const dialog = useModalDialog({
    onClose,
    discardMessage: 'Discard your unsaved changes to this review?',
  });
  const titleId = `finding-review-title-${kind}`;

  return (
    <div className="modal-backdrop" role="presentation" {...dialog.backdropProps}>
      <div
        className="modal-surface surface-card variant-review-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        ref={dialog.dialogRef}
        tabIndex={-1}
        {...dialog.surfaceProps}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">{kind === 'repeat' ? 'Repeat review' : 'Paraphase review'}</p>
            <h2 id={titleId} className="catalog-card-title">
              {title}
            </h2>
            {subtitle ? <p className="variant-review-modal-subtitle">{subtitle}</p> : null}
          </div>
          <button type="button" className="button-secondary" onClick={dialog.requestClose}>
            Close
          </button>
        </div>
        <div className="variant-review-modal-body">
          {errorMessage ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">{errorMessage}</div>
          ) : null}
          <section className="variant-review-modal-section">
            <div className="variant-review-note-header">
              <p className="analysis-section-title">Review</p>
              <p className="table-subtle">
                Your classification stands beside CoGA&apos;s automatic status and never changes it.
              </p>
            </div>
            <div className="variant-review-curation-columns">
              <fieldset className="variant-review-tag-column">
                <legend className="variant-annotation-impact-title">Classification</legend>
                <div className="variant-review-tag-list">
                  {reviewScale(kind).map((option) => (
                    <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={classification === option.value}
                        onChange={() => setClassification((current) => (current === option.value ? '' : option.value))}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="variant-review-tag-column">
                <legend className="variant-annotation-impact-title">Standard tags</legend>
                <div className="variant-review-tag-list">
                  {standardTagOptions.map((option) => (
                    <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={selectedTags.includes(option.value)}
                        onChange={() => toggleTag(option.value)}
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              </fieldset>
              <fieldset className="variant-review-tag-column">
                <legend className="variant-annotation-impact-title">Custom tags</legend>
                {customTagOptions.length || held.length ? (
                  <div className="variant-review-tag-list">
                    {[...customTagOptions, ...held].map((option) => (
                      <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                        <input
                          type="checkbox"
                          checked={selectedTags.includes(option.value)}
                          onChange={() => toggleTag(option.value)}
                        />
                        {option.label}
                        {isAcmgClassTag(option.value) ? (
                          <span className="table-subtle"> (an ACMG class tag, not offered here)</span>
                        ) : null}
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="table-subtle">No custom tags available.</p>
                )}
              </fieldset>
            </div>
            <label className="sr-only" htmlFor={`finding-review-note-${kind}`}>
              Note
            </label>
            <textarea
              id={`finding-review-note-${kind}`}
              className="variant-review-textarea"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              rows={3}
              placeholder="Note or rationale"
            />
          </section>
        </div>
        {readOnly ? <ReadOnlyNote /> : null}
        <div className="variant-search-actions variant-review-modal-actions">
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              setClassification('');
              setSelectedTags([]);
              setNote('');
            }}
          >
            Clear
          </button>
          <button
            type="button"
            className="form-button"
            disabled={readOnly || isPending}
            title={readOnly ? READ_ONLY_HINT : undefined}
            onClick={() => {
              void onSave({
                classification: classification || null,
                tags: selectedTags,
                note: note.trim() || null,
              }).then((saved) => {
                if (saved) onClose();
              });
            }}
          >
            {isPending ? 'Saving…' : 'Save review'}
          </button>
        </div>
      </div>
    </div>
  );
}
