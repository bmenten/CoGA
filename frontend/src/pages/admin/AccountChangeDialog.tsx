import React, { useId } from 'react';
import ModalDialog from '../../components/ModalDialog';

interface AccountChangeDialogProps {
  /** The dialog's heading and accessible name ("Activate ann@example.com?"). */
  title: string;
  /** The confirming button's label ("Activate"). */
  confirmLabel: string;
  /** While the change is sent: both buttons are disabled and Escape does nothing. */
  busy?: boolean;
  onCancel: () => void;
  onConfirm: () => void;
  /** Which account, and what the change does to it. */
  children: React.ReactNode;
}

/**
 * Asks before an account's activation, role or archiving is changed on the Users page: nothing
 * is sent until the confirming button is pressed. Cancel, Escape and the backdrop send
 * nothing, and focus starts on Cancel, so Enter on the opened dialog cancels. A deletion asks
 * with `ConfirmDeleteDialog`.
 */
const AccountChangeDialog: React.FC<AccountChangeDialogProps> = ({
  title,
  confirmLabel,
  busy = false,
  onCancel,
  onConfirm,
  children,
}) => {
  const headingId = useId();
  return (
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
        <p className="page-kicker">Confirm the change</p>
        <h2 id={headingId} className="section-title">
          {title}
        </h2>
      </div>
      <div className="space-y-2">{children}</div>
      <div className="inline-actions modal-actions">
        <button type="button" className="button-secondary" onClick={onCancel} disabled={busy}>
          Cancel
        </button>
        <button type="button" className="form-button" onClick={onConfirm} disabled={busy}>
          {busy ? 'Saving…' : confirmLabel}
        </button>
      </div>
    </ModalDialog>
  );
};

export default AccountChangeDialog;
