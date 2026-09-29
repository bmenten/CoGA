import React from 'react';
import { useModalDialog } from '../lib/useModalDialog';

/**
 * A modal dialog shell with the behaviour of `useModalDialog` (#529): Escape, a focus
 * trap and focus restore, and a check before unsaved input is discarded. For dialogs
 * rendered inline and conditionally, where a hook cannot be called directly. Children
 * may be a function of `requestClose`, for Cancel/Close buttons that should ask too.
 */
const ModalDialog: React.FC<{
  onClose: () => void;
  /** Accessible name: the id of a heading inside, or a label. */
  labelledBy?: string;
  label?: string;
  className?: string;
  backdropClassName?: string;
  /** Whether a click on the backdrop closes the dialog (default true). */
  closeOnBackdrop?: boolean;
  confirmDiscard?: boolean;
  discardMessage?: string;
  /** Whether closing now would lose input; see `useModalDialog`. */
  isDirty?: () => boolean;
  children: React.ReactNode | ((requestClose: () => void) => React.ReactNode);
}> = ({
  onClose,
  labelledBy,
  label,
  className = 'modal-surface surface-card',
  backdropClassName = 'modal-backdrop',
  closeOnBackdrop = true,
  confirmDiscard = true,
  discardMessage,
  isDirty,
  children,
}) => {
  const { dialogRef, requestClose, surfaceProps, backdropProps } = useModalDialog({
    onClose,
    confirmDiscard,
    discardMessage,
    isDirty,
  });
  return (
    <div
      className={backdropClassName}
      role="presentation"
      {...(closeOnBackdrop ? backdropProps : {})}
    >
      <div
        ref={dialogRef}
        className={className}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-label={labelledBy ? undefined : label}
        tabIndex={-1}
        {...surfaceProps}
      >
        {typeof children === 'function' ? children(requestClose) : children}
      </div>
    </div>
  );
};

export default ModalDialog;
