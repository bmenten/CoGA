import {
  useCallback,
  useLayoutEffect,
  useRef,
  useState,
  type MouseEvent,
  type RefObject,
} from 'react';

/**
 * Keyboard, focus and close behaviour for a modal dialog (#529).
 *
 * The review and ACMG dialogs closed on any backdrop click, with no check for unsaved
 * input, no Escape key and no focus handling. With this hook a dialog:
 *
 * - closes on Escape and on a backdrop click that both starts and ends on the backdrop
 *   (dragging a text selection out of a field no longer closes it);
 * - asks before closing when the user has changed any field since it opened;
 * - moves focus into the dialog, keeps Tab inside it, and gives focus back on close.
 *
 * "Changed" means a change or input event from a form control inside the dialog, so
 * values a dialog fills in itself (auto-suggested criteria) do not count. A dialog whose
 * edits can be applied without closing it passes `isDirty` instead, so that applied
 * edits no longer count.
 */
export interface ModalDialogOptions {
  onClose: () => void;
  /** Asked before closing a dialog that has unsaved input. */
  discardMessage?: string;
  /** False for dialogs whose input is only ever a deliberate submission. */
  confirmDiscard?: boolean;
  /** Whether closing now would lose input, read from the dialog's own state. */
  isDirty?: () => boolean;
}

export interface ModalDialog {
  dialogRef: RefObject<HTMLDivElement | null>;
  /** Close, asking first when there is unsaved input. For every Cancel/Close control. */
  requestClose: () => void;
  /** Spread onto the dialog surface: tracks unsaved input. */
  surfaceProps: {
    onChangeCapture: () => void;
    onInputCapture: () => void;
  };
  /** Spread onto the backdrop. */
  backdropProps: {
    onMouseDown: (event: MouseEvent<HTMLElement>) => void;
    onClick: (event: MouseEvent<HTMLElement>) => void;
  };
}

const FOCUSABLE =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

// Open dialogs, innermost last: only the topmost one answers Escape and traps Tab.
const openDialogs: object[] = [];

const focusableIn = (root: HTMLElement): HTMLElement[] =>
  Array.from(root.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (element) => !element.hasAttribute('disabled') && element.getAttribute('aria-hidden') !== 'true',
  );

export function useModalDialog({
  onClose,
  discardMessage = 'Discard your unsaved changes?',
  confirmDiscard = true,
  isDirty,
}: ModalDialogOptions): ModalDialog {
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const dirtyRef = useRef(false);
  const pressStartedOnBackdrop = useRef(false);
  // Read while rendering: by the time an effect runs, an autoFocus control inside the
  // dialog has taken focus, or the dialog underneath has gone inert and dropped it.
  const [previouslyFocused] = useState(() =>
    document.activeElement instanceof HTMLElement ? document.activeElement : null,
  );
  const onCloseRef = useRef(onClose);
  const isDirtyRef = useRef(isDirty);
  useLayoutEffect(() => {
    onCloseRef.current = onClose;
    isDirtyRef.current = isDirty;
  });

  const requestClose = useCallback(() => {
    const dirty = isDirtyRef.current ? isDirtyRef.current() : dirtyRef.current;
    if (confirmDiscard && dirty && !window.confirm(discardMessage)) return;
    onCloseRef.current();
  }, [confirmDiscard, discardMessage]);

  const requestCloseRef = useRef(requestClose);
  useLayoutEffect(() => {
    requestCloseRef.current = requestClose;
  });

  // A layout effect, so the dialog answers Escape from the commit that draws it: in a
  // passive effect, which runs in a later task, an Escape pressed in between reached the
  // dialog underneath (#529).
  useLayoutEffect(() => {
    const token = {};
    openDialogs.push(token);
    const dialog = dialogRef.current;
    if (dialog && !dialog.contains(document.activeElement)) {
      const [first] = focusableIn(dialog);
      (first ?? dialog).focus();
    }

    const onKeyDown = (event: KeyboardEvent) => {
      const node = dialogRef.current;
      if (!node || openDialogs[openDialogs.length - 1] !== token) return;
      if (event.key === 'Escape') {
        event.preventDefault();
        event.stopPropagation();
        requestCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;
      const focusable = focusableIn(node);
      if (focusable.length === 0) {
        event.preventDefault();
        node.focus();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = document.activeElement;
      if (event.shiftKey && (active === first || !node.contains(active))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (active === last || !node.contains(active))) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('keydown', onKeyDown);
      openDialogs.splice(openDialogs.indexOf(token), 1);
      if (previouslyFocused?.isConnected) previouslyFocused.focus();
    };
  }, [previouslyFocused]);

  const markDirty = useCallback(() => {
    dirtyRef.current = true;
  }, []);

  return {
    dialogRef,
    requestClose,
    surfaceProps: { onChangeCapture: markDirty, onInputCapture: markDirty },
    backdropProps: {
      onMouseDown: (event) => {
        pressStartedOnBackdrop.current = event.target === event.currentTarget;
      },
      onClick: (event) => {
        const startedOnBackdrop = pressStartedOnBackdrop.current;
        pressStartedOnBackdrop.current = false;
        if (event.target === event.currentTarget && startedOnBackdrop) requestClose();
      },
    },
  };
}
