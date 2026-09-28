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
