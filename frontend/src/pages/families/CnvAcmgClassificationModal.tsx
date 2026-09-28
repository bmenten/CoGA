import { useMemo, useState, type MouseEvent } from 'react';
import { useModalDialog } from '../../lib/useModalDialog';

import {
  buildInitialCnvSelections,
  cnvCriteriaForKind,
  cnvGeneCountHint,
  cnvKindForType,
  CNV_SECTION_TITLES,
  computeCnvClassification,
  evaluateCnv,
  type CnvKind,
  type CnvSelection,
} from '../../lib/cnvAcmg';
import CnvScaleBar from './CnvScaleBar';
import type {
  StructuralVariant,
  StructuralVariantReviewSavePayload,
} from './structuralVariantSearch';

type CnvAcmgClassificationModalProps = {
  variant: StructuralVariant;
  onClose: () => void;
  onSave: (payload: StructuralVariantReviewSavePayload) => Promise<void>;
  isPending?: boolean;
  errorMessage?: string | null;
};

const formatPoints = (value: number): string =>
  (value > 0 ? '+' : '') + value.toFixed(2).replace(/\.?0+$/, (m) => (m === '.00' ? '' : m));

export default function CnvAcmgClassificationModal({
  variant,
  onClose,
  onSave,
  isPending = false,
  errorMessage = null,
}: CnvAcmgClassificationModalProps) {
  const savedKind = variant.review?.cnv_acmg?.kind;
  const [kind, setKind] = useState<CnvKind>(savedKind ?? cnvKindForType(variant.type));

  const geneCount =
    typeof variant.gene_count === 'number'
      ? variant.gene_count
      : variant.gene_symbols?.length ?? null;
  const suggestions = useMemo(
    () =>
      evaluateCnv({
        type: variant.type,
        gene: variant.gene,
        genePli: variant.gene_pli ?? variant.annotation_extra?.pli ?? null,
        inheritance: variant.annotation_extra?.inheritance ?? null,
        geneCount,
      }),
    [variant, geneCount],
  );

  // Re-seed selections whenever the kind changes; saved selections apply only when
  // their kind matches what is being shown.
  const [selectionsByKind, setSelectionsByKind] = useState<Record<CnvKind, CnvSelection[]>>(() => {
    const saved = variant.review?.cnv_acmg?.criteria;
    const seed = (k: CnvKind) =>
      buildInitialCnvSelections(k, suggestions, savedKind === k ? saved : undefined);
    return { loss: seed('loss'), gain: seed('gain') };
  });

  const selections = selectionsByKind[kind];
  const classification = useMemo(
    () => computeCnvClassification(kind, selections),
    [kind, selections],
  );

  const updateSelection = (code: string, patch: Partial<CnvSelection>) => {
    setSelectionsByKind((current) => ({
      ...current,
      [kind]: current[kind].map((selection) =>
        selection.code === code ? { ...selection, ...patch } : selection,
      ),
    }));
  };

  const sections = useMemo(() => {
    const defs = cnvCriteriaForKind(kind);
    const grouped = new Map<string, typeof defs>();
    defs.forEach((def) => {
      const list = grouped.get(def.section) ?? [];
      list.push(def);
      grouped.set(def.section, list);
    });
    return Array.from(grouped.entries());
  }, [kind]);

  const selectionMap = useMemo(
    () => Object.fromEntries(selections.map((selection) => [selection.code, selection])),
    [selections],
  );

  const [tip, setTip] = useState<{ text: string; top: number; left: number } | null>(null);
  const showTip = (text: string) => (event: MouseEvent<HTMLElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    setTip({ text, top: rect.bottom + 6, left: rect.left });
  };
  const hideTip = () => setTip(null);

  const handleSave = async () => {
    const payload: StructuralVariantReviewSavePayload = {
      classification: classification.classLabel,
      tags: variant.review?.tags ?? [],
      note: variant.review?.note ?? undefined,
      cnv_acmg: {
        kind,
        criteria: selections.map((selection) => ({
          code: selection.code,
          points: selection.points,
          accepted: selection.accepted,
          evidence: selection.evidence ?? undefined,
          auto_suggested: selection.autoSuggested,
        })),
        point_total: classification.pointTotal,
        classification: classification.classLabel,
      },
    };
    await onSave(payload);
  };

  // Escape, focus trap, and a check before unsaved input is discarded (#529).
  const dialog = useModalDialog({
    onClose,
    discardMessage: 'Discard your unsaved changes to this CNV classification?',
  });

  return (
    <div className="modal-backdrop" role="presentation" {...dialog.backdropProps}>
      <div
        className="modal-surface surface-card variant-review-modal acmg-modal cnv-acmg-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="cnv-acmg-classification-title"
        ref={dialog.dialogRef}
        tabIndex={-1}
        {...dialog.surfaceProps}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">ClinGen CNV classification</p>
            <h2 id="cnv-acmg-classification-title" className="section-title">
              {variant.type || 'SV'} · {variant.chr}:{variant.start.toLocaleString()}-
              {variant.end.toLocaleString()}
              {variant.gene ? ` · ${variant.gene}` : ''}
            </h2>
            <p className="variant-review-modal-subtitle">
              Semi-automatic decision support (Riggs et al., 2020). Confirm every criterion before
              clinical use.
            </p>
          </div>
          <label className="variant-summary-select-field">
            <span>CNV type</span>
            <select value={kind} onChange={(event) => setKind(event.target.value as CnvKind)}>
              <option value="loss">Copy-number loss</option>
              <option value="gain">Copy-number gain</option>
            </select>
          </label>
        </div>

        <CnvScaleBar classification={classification} />

        <div className="variant-review-modal-body acmg-modal-body">
          {errorMessage ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">
              {errorMessage}
            </div>
          ) : null}
          {variant.review?.acmg_unreadable ? (
            <div
              className="variant-workspace-feedback variant-workspace-feedback--warning"
              role="alert"
            >
              The CNV classification stored for this variant could not be read, so its criteria are not
              shown below. Saving replaces it with what you select here.
            </div>
          ) : null}

          <div className="cnv-sections-grid">
          {sections.map(([section, defs]) => (
            <div key={section} className="acmg-criteria-section">
              <p className="variant-card-section-title">
                {CNV_SECTION_TITLES[section] || `Section ${section}`}
              </p>
              {section === '3' ? (
                <p className="cnv-section-hint">{cnvGeneCountHint(variant.type)}</p>
              ) : null}
              <div className="cnv-criteria-list">
                {defs.map((def) => {
                  const selection = selectionMap[def.code];
                  if (!selection) return null;
                  const adjustable = def.minPoints !== def.maxPoints;
                  const rangeLabel = adjustable
                    ? `allowed ${formatPoints(def.minPoints)} … ${formatPoints(def.maxPoints)}`
                    : `fixed ${formatPoints(def.defaultPoints)}`;
                  const tipText = `${def.code} — ${def.name} (${rangeLabel})`;
                  return (
                    <div
                      key={def.code}
                      className={`cnv-criterion-row${selection.accepted ? ' cnv-criterion-row--active' : ''}${
                        adjustable ? '' : ' cnv-criterion-row--fixed'
                      }`}
                      onMouseEnter={showTip(tipText)}
                      onMouseLeave={hideTip}
                    >
                      <label className="analysis-checkbox cnv-criterion-main">
                        <input
                          type="checkbox"
                          checked={selection.accepted}
                          aria-label={`${def.code}: ${def.name}`}
                          onChange={(event) =>
                            updateSelection(def.code, { accepted: event.target.checked })
                          }
                        />
                        <span className="cnv-criterion-code">{def.code}</span>
                        {selection.autoSuggested ? (
                          <span
                            className="acmg-criterion-flag acmg-criterion-flag--consider"
                            aria-label="Auto-suggested"
                          >
                            ●
                          </span>
                        ) : null}
                      </label>
                      <input
                        className="cnv-points-input"
                        type="number"
                        step={0.05}
                        min={def.minPoints}
                        max={def.maxPoints}
                        value={selection.points}
                        disabled={!adjustable}
                        title={rangeLabel}
                        onChange={(event) =>
                          updateSelection(def.code, {
                            points: Math.max(
                              def.minPoints,
                              Math.min(def.maxPoints, Number(event.target.value)),
                            ),
                          })
                        }
                      />
                      <input
                        className="cnv-evidence-field"
                        placeholder="Note"
                        value={selection.evidence ?? ''}
                        onChange={(event) =>
                          updateSelection(def.code, { evidence: event.target.value })
                        }
                      />
                    </div>
                  );
                })}
              </div>
            </div>
          ))}
          </div>
        </div>

        <div className="variant-search-actions variant-review-modal-actions">
          <button type="button" className="button-secondary" onClick={dialog.requestClose}>
            Cancel
          </button>
          <button type="button" className="form-button" disabled={isPending} onClick={handleSave}>
            {isPending ? 'Saving…' : 'Save classification'}
          </button>
        </div>

        {tip ? (
          <div className="cnv-tooltip" style={{ top: tip.top, left: tip.left }} role="tooltip">
            {tip.text}
          </div>
        ) : null}
      </div>
    </div>
  );
}
