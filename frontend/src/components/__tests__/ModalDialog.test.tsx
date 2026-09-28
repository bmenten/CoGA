// The dialog shell used by inline dialogs (report acknowledgements, Settings) — #529.

import { fireEvent, render, screen } from '@testing-library/react';
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
});
