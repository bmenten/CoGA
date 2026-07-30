import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminQcThresholdsPage from '../AdminQcThresholdsPage';
import api from '../../../lib/api';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), put: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);
const mockedPut = vi.mocked(api.put);

const CATALOGUE = {
  metrics: [
    {
      key: 'depth.mean_depth',
      label: 'Mean depth',
      unit: 'x',
      direction: 'lower_is_worse' as const,
      help_text: 'Genome-wide mean coverage from mosdepth.',
    },
    {
      key: 'reads.stdev_read_length',
      label: 'Read-length spread (SD)',
      unit: 'bp',
      direction: 'higher_is_worse' as const,
      help_text: 'Standard deviation of read length.',
    },
  ],
  profiles: [
    {
      id: 'p1',
      key: 'default',
      label: 'Default',
      description: 'Applies to any family that does not name a profile.',
      is_default: true,
      sort_order: 10,
      thresholds: [
        {
          metric_key: 'depth.mean_depth',
          label: 'Mean depth',
          unit: 'x',
          direction: 'lower_is_worse' as const,
          known_metric: true,
          warn_value: 20,
          error_value: 10,
        },
      ],
    },
    {
      id: 'p2',
      key: 'long_read_wgs',
      label: 'Long-read WGS',
      description: 'PacBio HiFi / ONT whole-genome runs.',
      is_default: false,
      sort_order: 20,
      thresholds: [],
    },
  ],
};

const renderPage = () => {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter>
        <AdminQcThresholdsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

describe('AdminQcThresholdsPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    mockedPut.mockReset();
    mockedGet.mockResolvedValue({ data: CATALOGUE });
    mockedPut.mockResolvedValue({ data: {} });
  });

  it('lists every catalogue metric with its unit and failing side', async () => {
    renderPage();

    expect(await screen.findByText('Mean depth')).toBeInTheDocument();
    expect(screen.getByText('Read-length spread (SD)')).toBeInTheDocument();
    // Direction comes from the backend catalogue, so the operator can see which way a
    // bound is applied instead of guessing.
    expect(screen.getByText('value below bound')).toBeInTheDocument();
    expect(screen.getByText('value above bound')).toBeInTheDocument();
  });

  it('starts on the default profile and shows its stored bounds', async () => {
    renderPage();

    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;
    const error = screen.getByLabelText('Mean depth error threshold') as HTMLInputElement;
    expect(warn.value).toBe('20');
    expect(error.value).toBe('10');
    expect(screen.getByText('20 / 10')).toBeInTheDocument();
  });

  it('switching profile shows that profile’s own bounds, not the default’s', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText('Mean depth warning threshold');

    await user.click(screen.getByRole('button', { name: /Long-read WGS/ }));

    const warn = screen.getByLabelText('Mean depth warning threshold') as HTMLInputElement;
    // A cut-off cannot leak across assays — that is the whole reason profiles exist.
    expect(warn.value).toBe('');
    expect(screen.getAllByText('not set').length).toBeGreaterThan(0);
  });

  it('saves an edited bound against the selected profile', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;

    await user.clear(warn);
    await user.type(warn, '15');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/qc-thresholds/default', {
        metric_key: 'depth.mean_depth',
        warn_value: 15,
        error_value: 10,
      }),
    );
  });

  it('clears a threshold when both bounds are blanked', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;
    const error = screen.getByLabelText('Mean depth error threshold') as HTMLInputElement;

    await user.clear(warn);
    await user.clear(error);
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    // Nulls clear the row: a metric with no cut-off is not configured, and must not be
    // left behind as an all-null row implying it is.
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/qc-thresholds/default', {
        metric_key: 'depth.mean_depth',
        warn_value: null,
        error_value: null,
      }),
    );
  });

  it('leaves Save disabled until a bound actually changes', async () => {
    renderPage();
    await screen.findByLabelText('Mean depth warning threshold');

    for (const button of screen.getAllByRole('button', { name: 'Save' })) {
      expect(button).toBeDisabled();
    }
  });

  it('says plainly that no cut-offs are shipped by default', async () => {
    renderPage();

    // Shipping invented clinical cut-offs would make an unreviewed number look
    // authoritative; the page has to say so.
    expect(
      await screen.findByText(/No values are shipped by default/i),
    ).toBeInTheDocument();
  });
});
