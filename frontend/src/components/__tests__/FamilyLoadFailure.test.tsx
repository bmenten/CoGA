import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import FamilyLoadFailure from '../FamilyLoadFailure';

const httpError = (status: number, detail?: string) =>
  Object.assign(new Error(`Request failed with status code ${status}`), {
    response: { status, data: detail ? { detail } : {} },
  });

// #610 — the family pages said "Family not found" for any failure, a server error included.
describe('FamilyLoadFailure', () => {
  it('says the family was not found on a 404, with no retry', () => {
    render(
      <FamilyLoadFailure
        kicker="mtDNA"
        what="Family"
        error={httpError(404, 'Family not found')}
        notFoundMessage="This mtDNA workspace could not resolve the requested family."
        onRetry={vi.fn()}
      />,
    );

    expect(screen.getByRole('heading', { name: 'Family not found' })).toBeInTheDocument();
    expect(screen.getByText('This mtDNA workspace could not resolve the requested family.')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
  });

  it('says any other failure is a failed request, with the reason and a retry', () => {
    const onRetry = vi.fn();
    render(
      <FamilyLoadFailure
        kicker="mtDNA"
        what="mtDNA analysis"
        error={httpError(503, 'ClickHouse is unavailable')}
        notFoundMessage="This mtDNA workspace could not resolve the requested family."
        onRetry={onRetry}
      />,
    );

    expect(screen.getByRole('heading', { name: 'mtDNA analysis could not be loaded' })).toBeInTheDocument();
    expect(
      screen.getByText('ClickHouse is unavailable This is a failed request, not a missing family.'),
    ).toBeInTheDocument();
    expect(screen.queryByText('Family not found')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('keeps the page action beside the retry, and alone when not found', () => {
    const action = <a href="/families">Back to families</a>;
    const { rerender } = render(
      <FamilyLoadFailure kicker="NIPT" what="Family" error={httpError(500)} notFoundMessage="" onRetry={vi.fn()} action={action} />,
    );
    expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to families' })).toBeInTheDocument();

    rerender(
      <FamilyLoadFailure kicker="NIPT" what="Family" error={httpError(404)} notFoundMessage="" onRetry={vi.fn()} action={action} />,
    );
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to families' })).toBeInTheDocument();
  });
});
