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

  it('opens a tandem duplication on gain, without the loss criterion 2H (CLIN-47)', () => {
    render(
      <CnvAcmgClassificationModal
        variant={{ ...variant, _id: 'DUP-1', type: 'DUP:TANDEM', gene_pli: 0.99 }}
        onClose={vi.fn()}
        onSave={noop}
      />,
    );

    expect((screen.getByRole('combobox') as HTMLSelectElement).value).toBe('gain');
    // 2H of a loss (haploinsufficiency predictors) is not suggested, so the gain's own 2H
    // starts unticked and carries no constrained-gene note.
    expect(screen.getByRole('checkbox', { name: /^2H:/ })).not.toBeChecked();
    expect(screen.queryByDisplayValue(/supports haploinsufficiency/)).not.toBeInTheDocument();
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

  it('names the caller of the row it was opened on, whose evidence the save freezes (TRACE-11)', async () => {
    const saves: StructuralVariantReviewSavePayload[] = [];
    const onSave = vi.fn(async (payload: StructuralVariantReviewSavePayload) => {
      saves.push(payload);
    });
    // Two callers' rows of one SV share its id; each dialog names its own row's caller.
    const { unmount } = render(
      <CnvAcmgClassificationModal variant={{ ...variant, source: 'needlr' }} onClose={vi.fn()} onSave={onSave} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));
    unmount();
    render(
      <CnvAcmgClassificationModal variant={{ ...variant, source: 'sniffles' }} onClose={vi.fn()} onSave={onSave} />,
    );
    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));

    expect(saves.map((payload) => payload.source)).toEqual(['needlr', 'sniffles']);
  });

  it('names no caller for a row without one, so the server takes the SV\'s one row', async () => {
    let saved: StructuralVariantReviewSavePayload | undefined;
    render(
      <CnvAcmgClassificationModal
        variant={variant}
        onClose={vi.fn()}
        onSave={async (payload) => {
          saved = payload;
        }}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));
    expect(saved?.source).toBeNull();
  });

  it('writes the criteria ranges and the point total in one format, to two decimals', () => {
    render(
      <CnvAcmgClassificationModal
        variant={{
          ...variant,
          review: {
            variant_id: variant._id,
            tags: [],
            tag_metadata: {},
            cnv_acmg: {
              kind: 'loss',
              criteria: [{ code: '2C-1', points: 0.9, accepted: true, auto_suggested: false }],
            },
          },
        }}
        onClose={vi.fn()}
        onSave={noop}
      />,
    );

    // The saved 2C-1 (0.90) plus the suggested 1A and 3A (0 each): the scale bar's total.
    expect(screen.getByText('+0.90 pts')).toBeInTheDocument();
    // Each criterion's allowed range, on its points field, carries the same two decimals.
    expect(screen.getByTitle('fixed +1.00')).toBeInTheDocument(); // 2A
    expect(screen.getByTitle('fixed -0.60')).toBeInTheDocument(); // 1B
    expect(screen.getByTitle('allowed -0.45 … +0.90')).toBeInTheDocument(); // 2E
    expect(screen.getAllByTitle('fixed 0.00').length).toBeGreaterThan(0); // 1A, 2G, 3A, 5F
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
