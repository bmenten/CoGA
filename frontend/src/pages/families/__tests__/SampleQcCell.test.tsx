import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import SampleQcCell from '../SampleQcCell';
import api from '../../../lib/api';
import type { ApiFamilyRecord } from '../../../lib/apiTypes';

vi.mock('../../../lib/api', () => ({
  default: { get: vi.fn() },
}));

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
    expect(screen.queryByRole('button', { name: /QC report/i })).not.toBeInTheDocument();
  });

  it('summarises depth, read-length N50 and read quality', () => {
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    expect(screen.getByText('18.6x · N50 14,283 bp · Q32')).toBeInTheDocument();
  });

  it('opens the report through a short-lived link in a new tab', async () => {
    mockedGet.mockResolvedValue({ data: { url: '/families/pacbio/qc-report/HG002?token=abc' } });
    const user = userEvent.setup();
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    await user.click(screen.getByRole('button', { name: /QC report/i }));

    await waitFor(() =>
      expect(mockedGet).toHaveBeenCalledWith('/families/pacbio/qc-report/HG002/link'),
    );
    // The report is untrusted pipeline HTML: it opens detached from this document.
    expect(window.open).toHaveBeenCalledWith(
      '/families/pacbio/qc-report/HG002?token=abc',
      '_blank',
      'noopener,noreferrer',
    );
  });

  it('surfaces an error instead of opening a tab when the link cannot be issued', async () => {
    mockedGet.mockRejectedValue(new Error('QC report service is unavailable'));
    const user = userEvent.setup();
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    await user.click(screen.getByRole('button', { name: /QC report/i }));

    await waitFor(() =>
      expect(screen.getByText('QC report service is unavailable')).toBeInTheDocument(),
    );
    expect(window.open).not.toHaveBeenCalled();
  });

  it('shows the metrics without a report button when only numbers were recorded', () => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={member({ sequencing_qc: { depth: { mean_depth: 30 } } })}
      />,
    );

    expect(screen.getByText('30.0x')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /QC report/i })).not.toBeInTheDocument();
  });
});
