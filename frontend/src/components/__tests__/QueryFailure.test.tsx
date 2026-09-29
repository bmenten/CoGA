// A failed request said where its result would be (#510, #606): never as an empty result,
// with the server's reason, what the failure means for the page, and a retry.
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import QueryFailure from '../QueryFailure';

describe('QueryFailure', () => {
  it('says what failed, why, and what it means, and retries', () => {
    const onRetry = vi.fn();
    render(
      <QueryFailure
        what="the gene panel list"
        error={{ response: { status: 503, data: { detail: 'The panel store is unavailable.' } } }}
        consequence="Panels cannot be chosen."
        onRetry={onRetry}
      />,
    );

    const failure = screen.getByRole('alert');
    expect(failure).toHaveTextContent(
      'Could not load the gene panel list — this is not an empty result. The panel store is unavailable. Panels cannot be chosen.',
    );
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it('needs neither a reason nor a retry', () => {
    render(<QueryFailure what="the coverage QC" />);

    expect(screen.getByRole('alert')).toHaveTextContent('Could not load the coverage QC — this is not an empty result.');
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });
});
