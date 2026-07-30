import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import SampleQcCell from '../SampleQcCell';
import api from '../../../lib/api';
import type { ApiFamilyRecord } from '../../../lib/apiTypes';

vi.mock('../../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../../lib/api')>('../../../lib/api');
  return {
    ...actual,
    default: { get: vi.fn(), defaults: { baseURL: '/api' } },
  };
});

const mockedGet = vi.mocked(api.get);

const member = (sampleMetadata?: Record<string, unknown>): ApiFamilyRecord['members'][number] =>
  ({
    sample_id: 'HG002',
    role: 'proband',
    affected: true,
    sex: 'male',
    sample_metadata: sampleMetadata,
  }) as ApiFamilyRecord['members'][number];

const QC = {
  sequencing_qc: {
    report: 'qc/nanoplot/HG002/HG002NanoPlot-report.html',
    reads: { read_length_n50: 14283, median_read_quality: 32.1 },
    depth: { mean_depth: 18.57 },
  },
};

describe('SampleQcCell', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    vi.spyOn(window, 'open').mockImplementation(() => null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('renders a placeholder when the sample has no sequencing QC', () => {
    render(<SampleQcCell familyId="pacbio" member={member()} />);

    expect(screen.getByText('-')).toBeInTheDocument();
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('shows depth and N50 short enough to fit a table cell, with the rest in the tooltip', async () => {
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    // The full string wrapped onto two lines in the members-table column, so the chip
    // carries only the two headline numbers.
    expect(screen.getByText('18.6x · N50 14 kb')).toBeInTheDocument();
    // The rest is revealed on hover through InfoTip, which appears immediately —
    // the native `title` tooltip only shows after a browser-controlled delay.
    await userEvent.hover(screen.getByRole('button'));
    expect(await screen.findByRole('tooltip')).toHaveTextContent('median read quality Q32');
  });

  it('opens the report through a short-lived link in a new tab', async () => {
    mockedGet.mockResolvedValue({ data: { url: '/families/pacbio/qc-report/HG002?token=abc' } });
    const user = userEvent.setup();
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    await user.click(screen.getByRole('button', { name: /18.6x/ }));

    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/families/pacbio/qc-report/HG002/link'),
    );
    // The API base must be prefixed: opened bare, the browser resolves the path
    // against the SPA origin and the client router renders "page not found".
    // The report is untrusted pipeline HTML, so it opens detached from this document.
    expect(window.open).toHaveBeenCalledWith(
      '/api/families/pacbio/qc-report/HG002?token=abc',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('uses an absolute presigned link unchanged (object-storage mode)', async () => {
    mockedGet.mockResolvedValue({
      data: { url: 'https://bucket.s3.amazonaws.com/families/pacbio/qc.html?sig=abc' },
    });
    const user = userEvent.setup();
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    await user.click(screen.getByRole('button', { name: /18.6x/ }));

    await waitFor(() =>
      expect(window.open).toHaveBeenCalledWith(
        'https://bucket.s3.amazonaws.com/families/pacbio/qc.html?sig=abc',
        '_blank',
        'noopener,noreferrer',
      ),
    );
  });

  it('surfaces an error instead of opening a tab when the link cannot be issued', async () => {
    mockedGet.mockRejectedValue(new Error('QC report service is unavailable'));
    const user = userEvent.setup();
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    await user.click(screen.getByRole('button', { name: /18.6x/ }));

    await waitFor(() =>
      expect(screen.getByText('QC report service is unavailable')).toBeInTheDocument(),
    );
    expect(window.open).not.toHaveBeenCalled();
  });

  it('shows the metrics as a plain chip when there is no report to open', () => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={member({ sequencing_qc: { depth: { mean_depth: 30 } } })}
      />,
    );

    expect(screen.getByText('30.0x')).toBeInTheDocument();
    // Nothing to open, so the chip must not look like a control.
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers the report even when no metrics were parsed', () => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={member({ sequencing_qc: { report: 'qc/nanoplot/HG002/report.html' } })}
      />,
    );

    expect(screen.getByRole('button', { name: /QC report/i })).toBeInTheDocument();
  });

  it('carries the metrics on the control itself rather than beside it', () => {
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    // One target, not a chip plus a button that said the same thing.
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(expect.stringContaining('18.6x'));
  });
});
