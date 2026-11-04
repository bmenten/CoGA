import React, { useId } from 'react';
import ModalDialog from './ModalDialog';

interface ConfirmDeleteDialogProps {
  /** The dialog's heading and accessible name, naming what goes ("Delete project X"). */
  title: string;
  /** The confirming button's label ("Delete project"). */
  confirmLabel: string;
  /** While the deletion runs: both buttons and Escape are disabled. */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  /** What the deletion takes with it. */
  children: React.ReactNode;
}

/**
 * Asks before a deletion is sent (UI-7), saying what goes. Nothing is sent until the
 * confirming button is pressed: Cancel, Escape and the backdrop send nothing. Focus starts on
 * Cancel, so Enter on the opened dialog cancels. A family's deletion asks more
 * (`DeleteFamilyDialog`): its name typed out and a reason for its audit trail.
 */
const ConfirmDeleteDialog: React.FC<ConfirmDeleteDialogProps> = ({
  title,
  confirmLabel,
  busy = false,
  onCancel,
  onConfirm,
  children,
}) => {
  const headingId = useId();
  return (
    // A click on the backdrop does nothing in a destructive confirmation, and nothing in it
    // is input to discard.
    <ModalDialog
      onClose={() => {
        if (!busy) onCancel();
      }}
      labelledBy={headingId}
      className="modal-surface surface-card confirm-delete-modal"
      closeOnBackdrop={false}
      confirmDiscard={false}
    >
      <div className="space-y-2">
        <p className="page-kicker page-kicker--danger">Permanent deletion</p>
        <h2 id={headingId} className="section-title">
          {title}
        </h2>
      </div>
      <div className="confirm-delete-warning">
        {children}
        <p className="confirm-delete-warning-strong">This cannot be undone.</p>
      </div>
      <div className="inline-actions modal-actions">
        <button type="button" className="button-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="button-danger" onClick={onConfirm} disabled={busy}>
          {busy ? 'Deleting…' : confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
};

export default ConfirmDeleteDialog;
