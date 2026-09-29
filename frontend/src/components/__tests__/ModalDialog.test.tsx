// The dialog shell used by inline dialogs (report acknowledgements, Settings) — #529.

import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';

import ModalDialog from '../ModalDialog';

describe('ModalDialog', () => {
  it('is a named modal dialog that closes on Escape', () => {
    const onClose = vi.fn();
    render(
      <ModalDialog label="User settings" onClose={onClose}>
        <button type="button">Save</button>
      </ModalDialog>,
    );
    const dialog = screen.getByRole('dialog', { name: 'User settings' });
    expect(dialog).toHaveAttribute('aria-modal', 'true');
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('can refuse to close on a backdrop click, for acknowledgement dialogs', () => {
    const onClose = vi.fn();
    const { container } = render(
      <ModalDialog label="Evidence drift acknowledgement required" closeOnBackdrop={false} onClose={onClose}>
        <textarea aria-label="Reason" />
      </ModalDialog>,
    );
    const backdrop = container.firstChild as HTMLElement;
    fireEvent.mouseDown(backdrop);
    fireEvent.click(backdrop);
    expect(onClose).not.toHaveBeenCalled();
  });

  it('asks before Escape discards a typed reason', () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    render(
      <ModalDialog label="Acknowledgement" discardMessage="Discard the reason you have typed?" onClose={onClose}>
        <textarea aria-label="Reason" />
      </ModalDialog>,
    );
    fireEvent.change(screen.getByLabelText('Reason'), { target: { value: 'because' } });
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(confirm).toHaveBeenCalledWith('Discard the reason you have typed?');
    expect(onClose).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  // A dialog on screen answers Escape at once. Its key handler used to be attached in a
  // passive effect, which runs in a later task than the one that draws the dialog: an
  // Escape in between reached the dialog underneath and closed that one instead (a
  // flaky test found it, #529).
  it('answers Escape as soon as it is on screen, not the dialog underneath', async () => {
    const closeOuter = vi.fn();
    let openInner: () => void = () => undefined;
    const Harness = () => {
      const [inner, setInner] = useState(false);
      openInner = () => setInner(true);
      return (
        <ModalDialog label="Upload" onClose={closeOuter}>
          {inner ? (
            <ModalDialog label="Overwrite" onClose={() => setInner(false)}>
              <button type="button">Cancel</button>
            </ModalDialog>
          ) : null}
        </ModalDialog>
      );
    };
    render(<Harness />);

    // Open the inner dialog as a response would (a default-priority update, outside
    // act), and press Escape the moment it is in the DOM.
    const shown = new Promise<void>((resolve) => {
      const observer = new MutationObserver(() => {
        if (screen.queryByRole('dialog', { name: 'Overwrite' })) {
          observer.disconnect();
          resolve();
        }
      });
      observer.observe(document.body, { childList: true, subtree: true });
    });
    const previous = (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT;
    (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = false;
    try {
      void Promise.resolve().then(() => openInner());
      await shown;
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
    } finally {
      (globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = previous;
    }

    expect(closeOuter).not.toHaveBeenCalled();
  });
});
