import React from 'react';
import { useModalDialog } from '../lib/useModalDialog';

interface AdminModalProps {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  /**
   * When another modal is stacked on top of this one, mark it inactive: the
   * dialog drops `aria-modal`, is hidden from assistive tech, and becomes
   * inert (non-focusable / non-interactive) so only the topmost dialog is
   * modal and keyboard focus can't reach the obscured content underneath.
   */
  inactive?: boolean;
}

/**
 * Lightweight admin dialog: backdrop + centered card with a title and close.
 *
 * Escape, a focus trap and focus restore come from `useModalDialog` (#529). It closes
 * without asking about unsaved input: these dialogs stay open once an import has
 * succeeded, so a change count would ask about input that was already submitted.
 */
const AdminModal: React.FC<AdminModalProps> = ({ title, onClose, children, inactive = false }) => {
  const { dialogRef, requestClose, backdropProps } = useModalDialog({
    onClose,
    confirmDiscard: false,
  });
  return (
    <div
      className="modal-backdrop"
      role="presentation"
      aria-hidden={inactive || undefined}
      inert={inactive}
      {...(inactive ? {} : backdropProps)}
    >
      <div
        ref={dialogRef}
        className="modal-surface surface-card admin-modal"
        role="dialog"
        aria-modal={inactive ? undefined : 'true'}
        aria-label={title}
        tabIndex={-1}
      >
        <div className="analysis-toolbar items-center">
          <h2 className="section-title">{title}</h2>
          <button
            type="button"
            className="button-ghost"
            style={{ marginLeft: 'auto' }}
            onClick={requestClose}
            aria-label="Close"
          >
            Close
          </button>
        </div>
        <div className="admin-modal-body">{children}</div>
      </div>
    </div>
  );
};

export default AdminModal;
