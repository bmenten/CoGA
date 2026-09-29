import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SmallVariantReviewDialog from '../SmallVariantReviewDialog';
import type { SmallVariantReview, SmallVariantReviewSavePayload } from '../smallVariantSearch';

// The review dialog is the tag and note editor for small and structural variants. It
// shows a classification as one of the ACMG class boxes; a stored label it cannot show
// (the ClinGen CNV dialog writes "Pathogenic - class 5" without a class tag) must survive
// a save in which the user leaves the classification alone.

const saveWith = async (
  review: Partial<SmallVariantReview>,
  act: () => void = () => undefined,
): Promise<SmallVariantReviewSavePayload> => {
  let saved: SmallVariantReviewSavePayload | undefined;
  const onSave = vi.fn(async (payload: SmallVariantReviewSavePayload) => {
    saved = payload;
  });
  render(
    <SmallVariantReviewDialog
      members={[]}
      variant={{
        _id: 'sv1',
        chr: 'chr1',
        start: 1000,
        end: 250000,
        type: 'DEL',
        review: { variant_id: 'sv1', tags: [], tag_metadata: {}, ...review } as SmallVariantReview,
      }}
      tags={[]}
      onClose={vi.fn()}
      onSave={onSave}
    />,
  );
  act();
  fireEvent.click(screen.getByRole('button', { name: /save review/i }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  return saved!;
};

const box = (label: string) => screen.getByRole('checkbox', { name: label });

describe('SmallVariantReviewDialog classification', () => {
  it('keeps a stored label it cannot show when the classification is left alone', async () => {
    const payload = await saveWith({
      classification: 'Pathogenic - class 5',
      tags: ['report'],
      note: 'scored with ClinGen',
    });
    expect(payload.classification).toBe('Pathogenic - class 5');
    expect(payload.tags).toEqual(['report']); // no class tag is added
    expect(payload.note).toBe('scored with ClinGen');
  });

  it('keeps that label when a class box is ticked and unticked again', async () => {
    const payload = await saveWith({ classification: 'Pathogenic - class 5' }, () => {
      fireEvent.click(box('VUS - class 3'));
      fireEvent.click(box('VUS - class 3'));
    });
    expect(payload.classification).toBe('Pathogenic - class 5');
    expect(payload.tags).toEqual([]);
  });

  it('replaces it with the class the user ticks', async () => {
    const payload = await saveWith({ classification: 'Pathogenic - class 5' }, () =>
      fireEvent.click(box('Likely Pathogenic - class 4')),
    );
    expect(payload.classification).toBe('Likely Pathogenic - class 4');
    expect(payload.tags).toEqual(['acmg_class_4']);
  });

  it('keeps a class shown from its tag as it is', async () => {
    const payload = await saveWith({
      classification: 'Likely Pathogenic - class 4',
      tags: ['acmg_class_4', 'report'],
    });
    expect(payload.classification).toBe('Likely Pathogenic - class 4');
    expect(payload.tags).toEqual(['acmg_class_4', 'report']);
  });

  it('sends an explicit clear when the user unticks the class shown', async () => {
    const payload = await saveWith(
      { classification: 'Likely Pathogenic - class 4', tags: ['acmg_class_4'] },
      () => fireEvent.click(box('Likely Pathogenic - class 4')),
    );
    expect(payload).toHaveProperty('classification', null);
    expect(payload.tags).toEqual([]);
  });

  it('sends no classification for a review that has none', async () => {
    const payload = await saveWith({ note: 'just a note' });
    expect(payload.classification).toBeUndefined();
  });
});
