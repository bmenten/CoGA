import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { formatDateTime } from '../../../lib/format';
import { review } from '../../../test/variants';
import ReviewAnnotations from '../ReviewAnnotations';
import { pendingNoteAuthorship } from '../smallVariantResultUtils';

const AT = '2026-11-21T10:00:00Z';

describe('ReviewAnnotations', () => {
  it('shows nothing for a review with no tag or note to show', () => {
    const { container } = render(<ReviewAnnotations review={review({ variant_id: 'V1' })} tagKeys={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it('opens the tag row with what the card adds, a review without tags or note too', () => {
    render(<ReviewAnnotations review={null} tagKeys={[]} leading={<span>Comp-het pair</span>} />);
    expect(screen.getByText('Comp-het pair').closest('.variant-card-tag-row')).toBeInTheDocument();
  });

  it('names who added the tags and wrote the note, and when', () => {
    const { container } = render(
      <ReviewAnnotations
        review={review({
          variant_id: 'V1',
          tags: ['mosaic', 'segregates'],
          tag_metadata: {
            mosaic: { updated_by: 'analyst', updated_at: AT },
            segregates: { updated_by: 'supervisor', updated_at: AT },
          },
          note: 'Line one.\nLine two.',
          note_updated_by: 'supervisor',
          note_updated_at: AT,
        })}
        tagKeys={['mosaic', 'segregates']}
      />,
    );
    // One save per analyst: each tag with its own line of who and when.
    expect(Array.from(container.querySelectorAll('.variant-card-review-tags')).map((save) => save.textContent)).toEqual([
      `mosaicanalyst · ${formatDateTime(AT)}`,
      `segregatessupervisor · ${formatDateTime(AT)}`,
    ]);
    expect(screen.getByText(/Line one\./)).toHaveClass('variant-card-review-note');
    expect(screen.getByText(/Line one\./)).toHaveTextContent(`supervisor · ${formatDateTime(AT)}`);
  });

  it('names no one for a tag or a note whose save is still on its way', () => {
    const { container } = render(
      <ReviewAnnotations
        review={review({ variant_id: 'V1', tags: ['mosaic'], note: 'New note.' })}
        tagKeys={['mosaic']}
      />,
    );
    expect(container.querySelector('.variant-card-review-author')).toBeNull();
    expect(screen.getByText('mosaic')).toBeInTheDocument();
    expect(screen.getByText('New note.')).toBeInTheDocument();
  });
});

describe('pendingNoteAuthorship', () => {
  const held = { note: 'Checked.', note_updated_by: 'analyst', note_updated_at: AT };

  it('keeps the author of the note a save leaves as it is', () => {
    expect(pendingNoteAuthorship(held, 'Checked.')).toEqual({ note_updated_by: 'analyst', note_updated_at: AT });
  });

  it('names no author for a new, changed or cleared note until the saved review does', () => {
    expect(pendingNoteAuthorship(held, 'Checked again.')).toEqual({ note_updated_by: null, note_updated_at: null });
    expect(pendingNoteAuthorship(null, 'Checked.')).toEqual({ note_updated_by: null, note_updated_at: null });
    expect(pendingNoteAuthorship(held, null)).toEqual({ note_updated_by: null, note_updated_at: null });
  });
});
