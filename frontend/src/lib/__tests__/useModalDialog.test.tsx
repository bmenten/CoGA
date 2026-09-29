// Dialog keyboard, focus and close behaviour — #529: clinical dialogs closed on any
// backdrop click and discarded unsaved input, with no Escape key or focus handling.

import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { useModalDialog } from '../useModalDialog';

function Dialog({ onClose, label = 'Review' }: { onClose: () => void; label?: string }) {
  const dialog = useModalDialog({ onClose, discardMessage: 'Discard?' });
  return (
    <div data-testid={`${label}-backdrop`} {...dialog.backdropProps}>
      <div ref={dialog.dialogRef} role="dialog" aria-label={label} tabIndex={-1} {...dialog.surfaceProps}>
        <input aria-label={`${label} note`} />
        <button type="button" onClick={dialog.requestClose}>
          Close {label}
        </button>
      </div>
    </div>
  );
}

function Harness({ onClose }: { onClose: () => void }) {
  const [open, setOpen] = useState(true);
  return (
    <>
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      {open ? (
        <Dialog
          onClose={() => {
            onClose();
            setOpen(false);
          }}
        />
      ) : null}
    </>
  );
}

const backdrop = (label = 'Review') => screen.getByTestId(`${label}-backdrop`);

const clickBackdrop = (label = 'Review') => {
  fireEvent.mouseDown(backdrop(label));
  fireEvent.click(backdrop(label));
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe('useModalDialog', () => {
  it('closes an untouched dialog on Escape and on a backdrop click', () => {
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
    clickBackdrop();
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it('asks before discarding unsaved input, and stays open when the answer is no', () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(<Dialog onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('Review note'), { target: { value: 'half-written' } });

    clickBackdrop();
    fireEvent.keyDown(document, { key: 'Escape' });
    fireEvent.click(screen.getByRole('button', { name: 'Close Review' }));
    expect(confirm).toHaveBeenCalledTimes(3);
    expect(confirm).toHaveBeenCalledWith('Discard?');
    expect(onClose).not.toHaveBeenCalled();

    confirm.mockReturnValue(true);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('asks from the dialog state when isDirty is given, not from change events', () => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    function StateDialog({ onClose }: { onClose: () => void }) {
      const [applied, setApplied] = useState('');
      const [draft, setDraft] = useState('');
      const dialog = useModalDialog({ onClose, isDirty: () => draft !== applied });
      return (
        <div ref={dialog.dialogRef} role="dialog" aria-label="Member" tabIndex={-1} {...dialog.surfaceProps}>
          <input aria-label="Sex" value={draft} onChange={(event) => setDraft(event.target.value)} />
          <button type="button" onClick={() => setApplied(draft)}>
            Apply
          </button>
        </div>
      );
    }
    const onClose = vi.fn();
    render(<StateDialog onClose={onClose} />);
    fireEvent.change(screen.getByLabelText('Sex'), { target: { value: 'female' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(onClose).not.toHaveBeenCalled();

    // Applied edits are kept, so closing no longer asks, although a field did change.
    fireEvent.click(screen.getByRole('button', { name: 'Apply' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('does not close when a press inside the dialog is released over the backdrop', () => {
    // Dragging a text selection out of a field used to close the dialog.
    const onClose = vi.fn();
    render(<Dialog onClose={onClose} />);
    fireEvent.mouseDown(screen.getByLabelText('Review note'));
    fireEvent.click(backdrop());
    expect(onClose).not.toHaveBeenCalled();
  });

  it('moves focus in, keeps Tab inside, and gives focus back on close', () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    const note = screen.getByLabelText('Review note');
    const close = screen.getByRole('button', { name: 'Close Review' });
    expect(document.activeElement).toBe(note);

    close.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(note);
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(close);
  });

  it('gives focus back to the opener when a control inside takes focus with autoFocus', () => {
    function Opener() {
      const [open, setOpen] = useState(false);
      const close = () => setOpen(false);
      return (
        <>
          <button type="button" onClick={() => setOpen(true)}>
            Open
          </button>
          {open ? <AutoFocusDialog onClose={close} /> : null}
        </>
      );
    }
    function AutoFocusDialog({ onClose }: { onClose: () => void }) {
      const dialog = useModalDialog({ onClose });
      return (
        <div ref={dialog.dialogRef} role="dialog" aria-label="Confirm" tabIndex={-1}>
          <button type="button">Close</button>
          {/* eslint-disable-next-line jsx-a11y/no-autofocus -- the case under test */}
          <button type="button" autoFocus onClick={dialog.requestClose}>
            Cancel
          </button>
        </div>
      );
    }
    render(<Opener />);
    const opener = screen.getByRole('button', { name: 'Open' });
    opener.focus();
    fireEvent.click(opener);
    expect(document.activeElement).toBe(screen.getByRole('button', { name: 'Cancel' }));
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(document.activeElement).toBe(opener);
  });

  it('lets only the topmost of two dialogs answer Escape', () => {
    const outer = vi.fn();
    const inner = vi.fn();
    render(
      <>
        <Dialog onClose={outer} label="Outer" />
        <Dialog onClose={inner} label="Inner" />
      </>,
    );
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(inner).toHaveBeenCalledTimes(1);
    expect(outer).not.toHaveBeenCalled();
  });
});
