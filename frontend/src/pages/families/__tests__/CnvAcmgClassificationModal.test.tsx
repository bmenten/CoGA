// CNV ACMG (ClinGen 2019) classification modal — REQ-CLASS-006 / REQ-UI-003 (risk H3).
// Decision support, not an autoclassifier: criteria are overridable, the kind
// (loss/gain) drives the applicable criteria, the classification recomputes from
// the selections, and Save emits a payload reflecting them.

import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';

import { CNV_GAIN_CRITERIA, CNV_LOSS_CRITERIA, type CnvCriterionDef } from '../../../lib/cnvAcmg';
import CnvAcmgClassificationModal from '../CnvAcmgClassificationModal';
import { FamilyReadOnlyContext } from '../../../lib/familyAccess';
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

// A 30-gene deletion in a constrained gene. As a loss, 25–34 genes are 3B (+0.45) and the
// pLI suggests 2H (+0.15); as a gain, 30 genes are 3A (0) and 2H is not suggested.
const constrainedDeletion: StructuralVariant = { ...variant, gene_count: 30, gene_pli: 0.99 };

const criterionBox = (code: string) => screen.getByRole('checkbox', { name: new RegExp(`^${code}:`) });
const criterionRow = (code: string) => criterionBox(code).closest('.cnv-criterion-row') as HTMLElement;

type SavedCnvScoring = NonNullable<StructuralVariantReviewSavePayload['cnv_acmg']>;

// What the server makes of a saved scoring (cnv_acmg_points, whose catalogues
// test_acmg_frontend_parity.py holds equal to the dialog's): each criterion of the kind once,
// its points kept within the criterion's range, the accepted ones summed to two decimals.
const serverRescoring = (scoring: SavedCnvScoring, catalogue: CnvCriterionDef[]) => {
  const byCode = new Map(catalogue.map((def) => [def.code, def]));
  const total = scoring.criteria
    .filter((criterion) => criterion.accepted)
    .reduce((sum, criterion) => {
      const def = byCode.get(criterion.code);
      return def ? sum + Math.max(def.minPoints, Math.min(def.maxPoints, criterion.points)) : sum;
    }, 0);
  return {
    codes: scoring.criteria.map((criterion) => criterion.code),
    outOfRange: scoring.criteria
      .filter((criterion) => {
        const def = byCode.get(criterion.code);
        return !def || criterion.points < def.minPoints || criterion.points > def.maxPoints;
      })
      .map((criterion) => `${criterion.code} ${criterion.points}`),
    pointTotal: Math.round(total * 100) / 100,
  };
};

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

  it("scores a deletion switched to gain with the gain's own suggestions (CLIN-55)", async () => {
    let saved: StructuralVariantReviewSavePayload | undefined;
    render(
      <CnvAcmgClassificationModal
        variant={constrainedDeletion}
        onClose={vi.fn()}
        onSave={async (payload) => {
          saved = payload;
        }}
      />,
    );
    // As a loss: 3B and 2H, with the constrained-gene note.
    expect(criterionBox('3B')).toBeChecked();
    expect(criterionBox('2H')).toBeChecked();
    expect(within(criterionRow('2H')).getByPlaceholderText('Note')).toHaveValue(
      'Constrained gene (pLI 0.990) supports haploinsufficiency.',
    );
    expect(screen.getByText('+0.60 pts')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole('combobox'), 'gain');

    // As a gain: the 30 genes are 3A, and neither the loss's 3B nor its 2H and note come along.
    expect(criterionBox('3A')).toBeChecked();
    expect(within(criterionRow('3A')).getByPlaceholderText('Note')).toHaveValue(
      '30 overlapped protein-coding genes.',
    );
    expect(criterionBox('3B')).not.toBeChecked();
    expect(criterionBox('2H')).not.toBeChecked();
    expect(screen.queryByDisplayValue(/supports haploinsufficiency/)).not.toBeInTheDocument();
    expect(screen.getByText('0.00 pts')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));

    const scoring = saved?.cnv_acmg as SavedCnvScoring;
    expect(scoring.kind).toBe('gain');
    const sent = Object.fromEntries(scoring.criteria.map((criterion) => [criterion.code, criterion]));
    expect(sent['3A']).toEqual({
      code: '3A',
      points: 0,
      accepted: true,
      evidence: '30 overlapped protein-coding genes.',
      auto_suggested: true,
    });
    expect(sent['3B']).toEqual({ code: '3B', points: 0.45, accepted: false, evidence: undefined, auto_suggested: false });
    expect(sent['2H']).toEqual({ code: '2H', points: 0, accepted: false, evidence: undefined, auto_suggested: false });
    expect(scoring.point_total).toBe(0);
    expect(scoring.classification).toBe('VUS - class 3');
    expect(saved?.classification).toBe('VUS - class 3');
    // The server keeps every value sent and recomputes the same total.
    expect(serverRescoring(scoring, CNV_GAIN_CRITERIA)).toEqual({
      codes: CNV_GAIN_CRITERIA.map((def) => def.code),
      outOfRange: [],
      pointTotal: 0,
    });
  });

  it("scores a duplication switched to loss with the loss's own suggestions (CLIN-55)", async () => {
    let saved: StructuralVariantReviewSavePayload | undefined;
    render(
      <CnvAcmgClassificationModal
        variant={{ ...constrainedDeletion, _id: 'DUP-1', type: 'DUP', gene_count: 40 }}
        onClose={vi.fn()}
        onSave={async (payload) => {
          saved = payload;
        }}
      />,
    );
    // As a gain, 40 genes are 3B (35–49).
    expect(criterionBox('3B')).toBeChecked();
    expect(screen.getByText('+0.45 pts')).toBeInTheDocument();

    await userEvent.selectOptions(screen.getByRole('combobox'), 'loss');

    // As a loss, 40 genes are 3C (35 or more), and the constrained gene suggests 2H.
    expect(criterionBox('3C')).toBeChecked();
    expect(criterionBox('3B')).not.toBeChecked();
    expect(criterionBox('2H')).toBeChecked();
    expect(screen.getByText('+1.05 pts')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));

    const scoring = saved?.cnv_acmg as SavedCnvScoring;
    expect(scoring.kind).toBe('loss');
    expect(scoring.criteria.filter((criterion) => criterion.accepted).map((criterion) => criterion.code)).toEqual([
      '1A',
      '2H',
      '3C',
    ]);
    expect(scoring.point_total).toBe(1.05);
    expect(scoring.classification).toBe('Pathogenic - class 5');
    expect(serverRescoring(scoring, CNV_LOSS_CRITERIA)).toEqual({
      codes: CNV_LOSS_CRITERIA.map((def) => def.code),
      outOfRange: [],
      pointTotal: 1.05,
    });
  });

  it('keeps what was changed by hand in the kind it was changed in (CLIN-55)', async () => {
    let saved: StructuralVariantReviewSavePayload | undefined;
    render(
      <CnvAcmgClassificationModal
        variant={constrainedDeletion}
        onClose={vi.fn()}
        onSave={async (payload) => {
          saved = payload;
        }}
      />,
    );
    // On loss: reject the suggested 2H, and score 4A with a note.
    await userEvent.click(criterionBox('2H'));
    await userEvent.click(criterionBox('4A'));
    fireEvent.change(within(criterionRow('4A')).getByRole('spinbutton'), { target: { value: '0.45' } });
    fireEvent.change(within(criterionRow('4A')).getByPlaceholderText('Note'), {
      target: { value: 'Reported de novo case.' },
    });

    // On gain, none of it: 4A unticked at 0 without the note, and the gain's own 2H unticked.
    await userEvent.selectOptions(screen.getByRole('combobox'), 'gain');
    expect(criterionBox('4A')).not.toBeChecked();
    expect(within(criterionRow('4A')).getByRole('spinbutton')).toHaveValue(0);
    expect(within(criterionRow('4A')).getByPlaceholderText('Note')).toHaveValue('');
    expect(criterionBox('2H')).not.toBeChecked();
    // A change on gain stays on gain.
    await userEvent.click(criterionBox('5A'));

    // Back on loss, the loss's changes are as they were left, and the gain's 5A is not there.
    await userEvent.selectOptions(screen.getByRole('combobox'), 'loss');
    expect(criterionBox('2H')).not.toBeChecked();
    expect(criterionBox('4A')).toBeChecked();
    expect(within(criterionRow('4A')).getByRole('spinbutton')).toHaveValue(0.45);
    expect(within(criterionRow('4A')).getByPlaceholderText('Note')).toHaveValue('Reported de novo case.');
    expect(criterionBox('5A')).not.toBeChecked();

    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));

    const scoring = saved?.cnv_acmg as SavedCnvScoring;
    expect(scoring.kind).toBe('loss');
    const sent = Object.fromEntries(scoring.criteria.map((criterion) => [criterion.code, criterion]));
    expect(sent['2H']).toMatchObject({ accepted: false, auto_suggested: true });
    expect(sent['4A']).toMatchObject({ accepted: true, points: 0.45, evidence: 'Reported de novo case.' });
    expect(sent['5A']).toMatchObject({ accepted: false });
    // 1A (0) + 3B (+0.45) + 4A (+0.45).
    expect(scoring.point_total).toBe(0.9);
    expect(scoring.classification).toBe('Likely Pathogenic - class 4');
    expect(serverRescoring(scoring, CNV_LOSS_CRITERIA).pointTotal).toBe(0.9);
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
    // Two callers' rows of one SV share its id; each dialog names its own row's caller, in its
    // header too (TRACE-12: the header named none, so the two dialogs looked the same).
    const { unmount } = render(
      <CnvAcmgClassificationModal variant={{ ...variant, source: 'needlr' }} onClose={vi.fn()} onSave={onSave} />,
    );
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/· GENEX · needlr call$/);
    await userEvent.click(screen.getByRole('button', { name: /save classification/i }));
    unmount();
    render(
      <CnvAcmgClassificationModal variant={{ ...variant, source: 'sniffles' }} onClose={vi.fn()} onSave={onSave} />,
    );
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/· GENEX · sniffles call$/);
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
    // Its header names no caller either.
    expect(screen.getByRole('heading', { level: 2 })).toHaveTextContent(/· GENEX$/);
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

// SEC-1: a reader in the family's projects sees the scoring but cannot save it.
describe('CnvAcmgClassificationModal for a reader', () => {
  it('does not save a reader’s classification and says why', () => {
    const onSave = vi.fn(async () => undefined);
    render(
      <FamilyReadOnlyContext.Provider value>
        <CnvAcmgClassificationModal variant={variant} onClose={vi.fn()} onSave={onSave} />
      </FamilyReadOnlyContext.Provider>,
    );
    const save = screen.getByRole('button', { name: /save classification/i });
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(onSave).not.toHaveBeenCalled();
    expect(screen.getByRole('note')).toHaveTextContent(/you are a reader in this family/);
  });
});
