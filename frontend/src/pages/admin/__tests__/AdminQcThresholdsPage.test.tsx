import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import AdminQcThresholdsPage from '../AdminQcThresholdsPage';
import api from '../../../lib/api';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn(), put: vi.fn(), post: vi.fn() },
}));

const mockedGet = vi.mocked(api.get);
const mockedPut = vi.mocked(api.put);
const mockedPost = vi.mocked(api.post);

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
  changes: [],
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
    mockedPost.mockReset();
    mockedGet.mockResolvedValue({ data: CATALOGUE });
    mockedPut.mockResolvedValue({ data: {} });
    mockedPost.mockResolvedValue({ data: { key: 'pgt' } });
  });

  it('lists every catalogue metric with its unit and failing side', async () => {
    renderPage();

    expect(await screen.findByText('Mean depth')).toBeInTheDocument();
    expect(screen.getByText('Read-length spread (SD)')).toBeInTheDocument();
    // Unit and direction sit inline with the metric now rather than in columns of
    // their own, but the operator must still be able to see which way a bound is
    // applied instead of guessing.
    expect(screen.getByText(/^x ↓$/)).toBeInTheDocument();
    expect(screen.getByText(/^bp ↑$/)).toBeInTheDocument();
  });

  it('starts on the default profile and shows its stored bounds', async () => {
    renderPage();

    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;
    const error = screen.getByLabelText('Mean depth error threshold') as HTMLInputElement;
    expect(warn.value).toBe('20');
    expect(error.value).toBe('10');
    // The stored bound is also the placeholder, so an emptied field still shows what
    // it would revert to.
    expect(warn.placeholder).toBe('20');
    expect(error.placeholder).toBe('10');
  });

  it('switching profile shows that profile’s own bounds, not the default’s', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText('Mean depth warning threshold');

    await user.click(screen.getByRole('button', { name: /Long-read WGS/ }));

    const warn = screen.getByLabelText('Mean depth warning threshold') as HTMLInputElement;
    // A cut-off cannot leak across assays — that is the whole reason profiles exist.
    expect(warn.value).toBe('');
    expect(warn.placeholder).toBe('none');
  });

  it('saves an edited bound against the selected profile', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;

    await user.clear(warn);
    await user.type(warn, '15');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);
    await user.type(await screen.findByLabelText(/Reason for the change/i), 'per VAL-P07');
    await user.click(screen.getByRole('button', { name: /Confirm change/i }));

    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/qc-thresholds/default', {
        metric_key: 'depth.mean_depth',
        warn_value: 15,
        error_value: 10,
        reason: 'per VAL-P07',
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
    await user.type(await screen.findByLabelText(/Reason for the change/i), 'per VAL-P07');
    await user.click(screen.getByRole('button', { name: /Confirm change/i }));

    // Nulls clear the row: a metric with no cut-off is not configured, and must not be
    // left behind as an all-null row implying it is.
    await waitFor(() =>
      expect(mockedPut).toHaveBeenCalledWith('/admin/qc-thresholds/default', {
        metric_key: 'depth.mean_depth',
        warn_value: null,
        error_value: null,
        reason: 'per VAL-P07',
      }),
    );
  });

  it('offers no Save until a bound actually changes', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;

    // Sixteen permanently disabled buttons were sixteen rows of noise; the control
    // appears on the row being edited.
    expect(screen.queryByRole('button', { name: 'Save' })).not.toBeInTheDocument();

    await user.clear(warn);
    await user.type(warn, '15');
    expect(screen.getAllByRole('button', { name: 'Save' })).toHaveLength(1);
  });

  it('does not write until the change is confirmed', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;

    await user.clear(warn);
    await user.type(warn, '15');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    // A cut-off decides whether a run is reported as inadequate; it must not be committed
    // on one stray click. The dialog restates both sides of the change.
    const dialog = await screen.findByRole('dialog');
    expect(dialog).toHaveTextContent(/warning 20 → 15/);
    expect(dialog).toHaveTextContent(/error 10 → 10/);
    expect(mockedPut).not.toHaveBeenCalled();

    await user.click(screen.getByRole('button', { name: /Cancel/i }));
    expect(mockedPut).not.toHaveBeenCalled();
  });

  it('shows the append-only change history with the value each edit replaced', async () => {
    mockedGet.mockResolvedValue({
      data: {
        ...CATALOGUE,
        changes: [
          {
            profile_key: 'default',
            metric_key: 'depth.mean_depth',
            previous_warn_value: 30,
            previous_error_value: 20,
            warn_value: 20,
            error_value: 10,
            changed_by_email: 'admin@example.com',
            changed_at: '2026-07-30T09:15:00',
          },
        ],
      },
    });
    renderPage();

    expect(await screen.findByText('Change history')).toBeInTheDocument();
    expect(screen.getByText('admin@example.com')).toBeInTheDocument();
    // The replaced value is the thing the HTTP request log cannot record.
    expect(screen.getByText('30 → 20')).toBeInTheDocument();
    expect(screen.getByText('20 → 10')).toBeInTheDocument();
  });

  it('says plainly that no cut-offs are shipped by default', async () => {
    renderPage();

    // Shipping invented clinical cut-offs would make an unreviewed number look
    // authoritative; the page has to say so.
    expect(await screen.findByText(/none are shipped by default/i)).toBeInTheDocument();
  });

  it('lists profiles and shows which of them actually gate anything', async () => {
    renderPage();

    // "Default" and "Long-read WGS" look identical in a row of buttons; the count is
    // the only thing that says one has cut-offs and the other does not.
    expect(await screen.findByRole('button', { name: /Default.*default.*1 set/s })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Long-read WGS.*none set/s })).toBeInTheDocument();
  });

  it('adds a profile and switches to it', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText('Mean depth warning threshold');

    await user.click(screen.getByRole('button', { name: /Add profile/i }));
    await user.type(screen.getByLabelText('New profile name'), 'PGT');
    await user.click(screen.getByRole('button', { name: /^Add$/ }));

    await waitFor(() =>
      expect(mockedPost).toHaveBeenCalledWith('/admin/qc-thresholds/profiles', { label: 'PGT' }),
    );
    // A new profile carries no inherited cut-offs, so the page has to say it gates
    // nothing yet rather than let it look configured.
    expect(await screen.findByText(/gates nothing until cut-offs are entered/i)).toBeInTheDocument();
  });

  it('will not submit an empty profile name', async () => {
    const user = userEvent.setup();
    renderPage();
    await screen.findByLabelText('Mean depth warning threshold');

    await user.click(screen.getByRole('button', { name: /Add profile/i }));
    expect(screen.getByRole('button', { name: /^Add$/ })).toBeDisabled();
    expect(mockedPost).not.toHaveBeenCalled();
  });

  it('will not commit a cut-off change without a reason', async () => {
    const user = userEvent.setup();
    renderPage();
    const warn = (await screen.findByLabelText('Mean depth warning threshold')) as HTMLInputElement;

    await user.clear(warn);
    await user.type(warn, '15');
    await user.click(screen.getAllByRole('button', { name: 'Save' })[0]);

    // The values either side are recorded automatically; what they cannot say is
    // whether a limit moved because a validation study supported it or because a run
    // was inconvenient.
    const confirm = await screen.findByRole('button', { name: /Confirm change/i });
    expect(confirm).toBeDisabled();
    expect(mockedPut).not.toHaveBeenCalled();

    await user.type(screen.getByLabelText(/Reason for the change/i), 'per VAL-P07');
    expect(screen.getByRole('button', { name: /Confirm change/i })).toBeEnabled();
  });

  it('shows the recorded reason in the change history', async () => {
    mockedGet.mockResolvedValue({
      data: {
        ...CATALOGUE,
        changes: [
          {
            profile_key: 'default',
            metric_key: 'depth.mean_depth',
            previous_warn_value: 30,
            previous_error_value: 20,
            warn_value: 20,
            error_value: 10,
            changed_by_email: 'admin@example.com',
            changed_at: '2026-07-30T09:15:00',
            reason: 'per VAL-P07 opvolgvalidatie',
          },
        ],
      },
    });
    renderPage();

    expect(await screen.findByText('per VAL-P07 opvolgvalidatie')).toBeInTheDocument();
  });
});
