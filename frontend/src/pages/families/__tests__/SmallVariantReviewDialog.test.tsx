import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import SmallVariantReviewDialog from '../SmallVariantReviewDialog';
import type {
  SmallVariantReview,
  SmallVariantReviewSavePayload,
  SmallVariantTagDefinition,
} from '../smallVariantSearch';

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

// A deleted custom tag stays on the reviews that hold it. The tag lists serve it flagged
// inactive; the dialog lists it, marked, where the review holds it, so it can be removed,
// and offers it to no other review.
const tagDefinition = (
  key: string,
  label: string,
  fields: Partial<SmallVariantTagDefinition> = {},
): SmallVariantTagDefinition => ({
  key,
  label,
  group: 'custom',
  color: '#336699',
  sort_order: 500,
  scope: 'global',
  is_custom: true,
  is_active: true,
  ...fields,
});

const TAG_DEFINITIONS: SmallVariantTagDefinition[] = [
  tagDefinition('review', 'Review', { group: 'collaboration', scope: 'system', is_custom: false }),
  tagDefinition('report', 'Report', { group: 'collaboration', scope: 'system', is_custom: false }),
  tagDefinition('needs_segregation', 'Needs segregation'),
  tagDefinition('probe_x', 'Probe X', { is_active: false }),
];

const renderWithTags = (reviewTags: string[], tagDefinitions = TAG_DEFINITIONS) => {
  const onSave = vi.fn(async (payload: SmallVariantReviewSavePayload) => {
    void payload;
  });
  render(
    <SmallVariantReviewDialog
      members={[]}
      variant={{
        _id: '1-1000-A-G',
        chr: '1',
        start: 1000,
        end: 1000,
        type: 'SNV',
        review: { variant_id: '1-1000-A-G', tags: reviewTags, tag_metadata: {} } as SmallVariantReview,
      }}
      tags={tagDefinitions}
      onClose={vi.fn()}
      onSave={onSave}
    />,
  );
  const save = async (): Promise<SmallVariantReviewSavePayload> => {
    fireEvent.click(screen.getByRole('button', { name: /save review/i }));
    await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
    return onSave.mock.calls[0][0];
  };
  return { save };
};

describe('SmallVariantReviewDialog deleted tags', () => {
  it('lists a deleted tag the review holds, marked and ticked, and keeps it on save', async () => {
    const { save } = renderWithTags(['probe_x', 'report']);
    expect(box('Probe X (deleted)')).toBeChecked();
    expect(box('Needs segregation')).not.toBeChecked();
    expect((await save()).tags).toEqual(['probe_x', 'report']);
  });

  it('removes a deleted tag the user unticks', async () => {
    const { save } = renderWithTags(['probe_x', 'report']);
    fireEvent.click(box('Probe X (deleted)'));
    expect((await save()).tags).toEqual(['report']);
  });

  it('offers a deleted tag to no review that does not hold it', () => {
    renderWithTags(['report']);
    expect(screen.queryByRole('checkbox', { name: /Probe X/ })).toBeNull();
    expect(box('Needs segregation')).not.toBeChecked();
  });

  it('lists a key the tag list does not hold as it is, so it can be removed', async () => {
    const { save } = renderWithTags(['legacy_key', 'report']);
    expect(box('legacy_key')).toBeChecked();
    fireEvent.click(box('legacy_key'));
    expect((await save()).tags).toEqual(['report']);
  });

  it('lists no held tag while the tag list is still loading', () => {
    renderWithTags(['probe_x', 'report'], []);
    expect(screen.queryByRole('checkbox', { name: /probe_x|Probe X/ })).toBeNull();
  });
});
