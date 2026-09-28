// CNV ACMG (ClinGen 2019) classification modal — REQ-CLASS-006 / REQ-UI-003 (risk H3).
// Decision support, not an autoclassifier: criteria are overridable, the kind
// (loss/gain) drives the applicable criteria, the classification recomputes from
// the selections, and Save emits a payload reflecting them.

import { fireEvent, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import CnvAcmgClassificationModal from '../CnvAcmgClassificationModal';
import type { StructuralVariant, StructuralVariantReviewSavePayload } from '../structuralVariantSearch';

const variant: StructuralVariant = {
  _id: 'DEL-1-1000-2000000',
  chr: 'chr1',
  start: 1000,
  end: 2000000,
  length: 1999000,
  type: 'DEL',
  gene: 'GENEX',
  gene_count: 5,
  genotypes: [],
};

const noop = vi.fn(async () => undefined);

describe('CnvAcmgClassificationModal', () => {
  it('renders the ClinGen decision-support header and confirm-before-use disclaimer', () => {
    render(<CnvAcmgClassificationModal variant={variant} onClose={vi.fn()} onSave={noop} />);

    expect(screen.getByText(/ClinGen CNV classification/i)).toBeInTheDocument();
    expect(screen.getByText(/Confirm every criterion before clinical use/i)).toBeInTheDocument();
  });

  it('defaults the kind to loss for a deletion and lets it be switched to gain', async () => {
    render(<CnvAcmgClassificationModal variant={variant} onClose={vi.fn()} onSave={noop} />);

    const select = screen.getByRole('combobox') as HTMLSelectElement;
    expect(select.value).toBe('loss');

    await userEvent.selectOptions(select, 'gain');
    expect(select.value).toBe('gain');
  });

  it('lets a criterion be overridden and reflects it in the saved payload', async () => {
    let saved: StructuralVariantReviewSavePayload | undefined;
    const onSave = vi.fn(async (payload: StructuralVariantReviewSavePayload) => {
      saved = payload;
    });
    render(<CnvAcmgClassificationModal variant={variant} onClose={vi.fn()} onSave={onSave} />);

    const checkboxes = screen.getAllByRole('checkbox') as HTMLInputElement[];
    expect(checkboxes.length).toBeGreaterThan(0);
    const target = checkboxes[0];
    const before = target.checked;
    await userEvent.click(target);
    expect(target.checked).toBe(!before); // criteria are overridable

    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));

    expect(onSave).toHaveBeenCalledTimes(1);
    expect(saved).toBeDefined();
    const acmg = saved?.cnv_acmg;
    expect(acmg?.kind).toBe('loss');
    expect(typeof acmg?.point_total).toBe('number');
    expect((acmg?.criteria ?? []).length).toBeGreaterThan(0);
    // The top-level classification mirrors the recomputed CNV classification.
    expect(saved?.classification).toBe(acmg?.classification);
  });

  it('warns before an unreadable stored CNV classification is overwritten (#514)', () => {
    render(
      <CnvAcmgClassificationModal
        variant={{
          ...variant,
          review: {
            variant_id: variant._id,
            tags: [],
            tag_metadata: {},
            cnv_acmg: null,
            acmg_unreadable: true,
          },
        }}
        onClose={vi.fn()}
        onSave={noop}
      />,
    );

    expect(screen.getByRole('alert')).toHaveTextContent(
      /The CNV classification stored for this variant could not be read.*Saving replaces it/,
    );
  });

  it('asks before a backdrop click discards changed criteria (#529)', () => {
    const onClose = vi.fn();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const { container } = render(
      <CnvAcmgClassificationModal variant={variant} onClose={onClose} onSave={noop} />,
    );
    expect(screen.getByRole('dialog', { name: /DEL · chr1/ })).toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('checkbox')[0]);
    const backdrop = container.querySelector('.modal-backdrop') as HTMLElement;
    fireEvent.mouseDown(backdrop);
    fireEvent.click(backdrop);
    fireEvent.keyDown(document, { key: 'Escape' });

    expect(confirm).toHaveBeenCalledTimes(2);
    expect(onClose).not.toHaveBeenCalled();
    confirm.mockRestore();
  });

  it('closes an untouched dialog on Escape', () => {
    const onClose = vi.fn();
    render(<CnvAcmgClassificationModal variant={variant} onClose={onClose} onSave={noop} />);
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('calls onClose from Cancel', async () => {
    const onClose = vi.fn();
    render(<CnvAcmgClassificationModal variant={variant} onClose={onClose} onSave={noop} />);

    await userEvent.click(screen.getByRole('button', { name: /^cancel$/i }));
    expect(onClose).toHaveBeenCalled();
  });
});
