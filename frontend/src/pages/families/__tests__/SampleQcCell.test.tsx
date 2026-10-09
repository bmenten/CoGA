import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import SampleQcCell from '../SampleQcCell';
import api from '../../../lib/api';
import type {
  ApiFamilyRecord,
  ApiSampleSequencingQcEvaluation,
  ApiSampleSequencingQcMetric,
} from '../../../lib/apiTypes';

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

/** One metric as the backend judges it against its limits (`evaluate_sequencing_qc`). */
const metric = (overrides: Partial<ApiSampleSequencingQcMetric> = {}): ApiSampleSequencingQcMetric => ({
  metric_key: 'depth.mean_depth',
  label: 'Mean depth',
  unit: 'x',
  direction: 'lower_is_worse',
  value: 18.57,
  warn_value: 20,
  error_value: 10,
  verdict: 'warn',
  ...overrides,
});

const DUPLICATES = {
  metric_key: 'alignment.duplicated_reads_percent',
  label: 'Duplicate reads',
  unit: '%',
  direction: 'higher_is_worse',
} as const;

/** The overall verdict the family read serves beside a member's recorded QC. */
const evaluation = (
  verdict: ApiSampleSequencingQcEvaluation['verdict'],
  metrics: ApiSampleSequencingQcMetric[],
): ApiSampleSequencingQcEvaluation => ({
  verdict,
  metrics,
  breached: metrics
    .filter((entry) => entry.verdict === 'warn' || entry.verdict === 'fail')
    .map((entry) => entry.metric_key),
  profile_key: 'default',
  profile_label: 'Default',
});

const judged = (
  verdict: ApiSampleSequencingQcEvaluation | null,
  sampleMetadata: Record<string, unknown> = QC,
): ApiFamilyRecord['members'][number] => ({ ...member(sampleMetadata), sequencing_qc: verdict });

/** The breach sentence leads the tooltip; the metrics and the profile follow after a dash. */
const breachSentence = (tooltip: HTMLElement): string => (tooltip.textContent ?? '').split(' — ')[0];

const TONES = ['family-qc-chip--neutral', 'table-chip--warning', 'table-chip--critical'];

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

  it('stays a compact pill: mean depth only, with the rest in the tooltip', async () => {
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    // The chip sits beside the Status pill and must stay that narrow, so it carries one
    // number; N50, quality and yield are on hover.
    expect(screen.getByText('18.6x')).toBeInTheDocument();
    expect(screen.queryByText(/N50/)).not.toBeInTheDocument();
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

  it("puts an embryo's PGT pipeline QC in the tooltip", async () => {
    render(
      <SampleQcCell
        familyId="PGT01"
        member={member({
          sequencing_qc: {
            report: 'qualimap/EMB1/qualimapReport.html',
            depth: { mean_depth: 9.5 },
            alignment: { mapped_reads_percent: 98.96, duplicated_reads_percent: 6.4 },
            sex_check: { method: 'ngs-bits SampleGender', inferred_sex: 'female' },
            pgt: {
              allele_dropout_rate: 12.35,
              allele_dropin_rate: 2.06,
              mendelian_concordance: 41.25,
              mendelian_concordance_imputed: 96.5,
            },
          },
        })}
      />,
    );

    // Depth stays the one number on the chip.
    expect(screen.getByText('9.5x')).toBeInTheDocument();
    await userEvent.hover(screen.getByRole('button'));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent('99.0% mapped');
    expect(tooltip).toHaveTextContent('6.4% duplicates');
    expect(tooltip).toHaveTextContent('ADO 12.3% · ADI 2.1%');
    expect(tooltip).toHaveTextContent('Mendelian concordance 41.3% before, 96.5% after imputation');
    expect(tooltip).toHaveTextContent('sex read as female (ngs-bits SampleGender)');
  });

  it('carries the metrics on the control itself rather than beside it', () => {
    render(<SampleQcCell familyId="pacbio" member={member(QC)} />);

    // One target, not a chip plus a button that said the same thing.
    const buttons = screen.getAllByRole('button');
    expect(buttons).toHaveLength(1);
    expect(buttons[0]).toHaveAccessibleName(expect.stringContaining('18.6x'));
  });
});

// REQ-QC-005 (TF-09a): the members table shows each sample's worst sequencing-QC state,
// distinguishable without relying on colour alone, and on inspection names the breaching
// metrics with their values and the limits they crossed.
describe('SampleQcCell sequencing-QC verdict', () => {
  it.each([
    {
      state: 'pass',
      verdict: evaluation('pass', [metric({ warn_value: 15, error_value: 10, verdict: 'pass' })]),
      name: '18.6x',
      tone: 'family-qc-chip--neutral',
      breach: null,
    },
    {
      state: 'warn',
      verdict: evaluation('warn', [metric({ warn_value: 20, error_value: 10, verdict: 'warn' })]),
      name: 'Warning 18.6x',
      tone: 'table-chip--warning',
      breach: 'Warning: Mean depth 18.57 below 20',
    },
    {
      state: 'fail',
      verdict: evaluation('fail', [metric({ warn_value: 30, error_value: 20, verdict: 'fail' })]),
      name: 'Fail 18.6x',
      tone: 'table-chip--critical',
      breach: 'Failed: Mean depth 18.57 below 20',
    },
    {
      state: 'not run (no limit configured)',
      verdict: evaluation('skip', [metric({ warn_value: null, error_value: null, verdict: 'skip' })]),
      name: '18.6x',
      tone: 'family-qc-chip--neutral',
      breach: null,
    },
    {
      state: 'not run (no verdict served)',
      verdict: null,
      name: '18.6x',
      tone: 'family-qc-chip--neutral',
      breach: null,
    },
  ])('$state: the chip reads "$name" in tone $tone', async ({ verdict, name, tone, breach }) => {
    render(<SampleQcCell familyId="pacbio" member={judged(verdict)} />);

    const chip = screen.getByRole('button');
    // A problem verdict is a word on the chip, so it does not rest on colour alone; a
    // passing or unassessed chip carries the number only.
    expect(chip).toHaveAccessibleName(name);
    expect(chip).toHaveClass('table-chip', 'family-qc-chip', tone);
    for (const other of TONES.filter((candidate) => candidate !== tone)) {
      expect(chip).not.toHaveClass(other);
    }

    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    if (breach) {
      // A warning is named against its warning limit, a failure against its error limit.
      expect(breachSentence(tooltip)).toBe(breach);
    } else {
      expect(tooltip).not.toHaveTextContent(/Warning:|Failed:/);
    }
  });

  it('names every breaching metric with its value and the limit it crossed, and no other', async () => {
    const verdict = evaluation('fail', [
      metric({ value: 8.2, warn_value: 20, error_value: 10, verdict: 'fail' }),
      metric({ ...DUPLICATES, value: 14.5, warn_value: 12, error_value: 20, verdict: 'warn' }),
      metric({
        metric_key: 'alignment.mapped_reads_percent',
        label: 'Mapped reads',
        unit: '%',
        value: 99.1,
        warn_value: 95,
        error_value: 90,
        verdict: 'pass',
      }),
      metric({
        metric_key: 'reads.read_length_n50',
        label: 'Read-length N50',
        unit: 'bp',
        value: 14283,
        warn_value: null,
        error_value: null,
        verdict: 'skip',
      }),
    ]);
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(verdict, {
          sequencing_qc: {
            report: 'qc/nanoplot/HG002/report.html',
            depth: { mean_depth: 8.2 },
            alignment: { duplicated_reads_percent: 14.5, mapped_reads_percent: 99.1 },
            reads: { read_length_n50: 14283 },
          },
        })}
      />,
    );

    const chip = screen.getByRole('button');
    expect(chip).toHaveAccessibleName('Fail 8.2x');
    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    // Each on the side its direction makes worse; the passing and the unassessed metric
    // are not named.
    expect(breachSentence(tooltip)).toBe(
      'Failed: Mean depth 8.2 below 10; Duplicate reads 14.5 above 12',
    );
    expect(tooltip).toHaveTextContent('Thresholds: Default profile');
  });

  it('shows the worst state when a metric other than the depth on the chip breached', async () => {
    const verdict = evaluation('fail', [
      metric({ value: 30, warn_value: 20, error_value: 10, verdict: 'pass' }),
      metric({ ...DUPLICATES, value: 25, warn_value: 12, error_value: 20, verdict: 'fail' }),
    ]);
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(verdict, {
          sequencing_qc: {
            report: 'qc/nanoplot/HG002/report.html',
            depth: { mean_depth: 30 },
            alignment: { duplicated_reads_percent: 25 },
          },
        })}
      />,
    );

    const chip = screen.getByRole('button');
    expect(chip).toHaveAccessibleName('Fail 30.0x');
    expect(chip).toHaveClass('table-chip--critical');
    await userEvent.hover(chip);
    expect(breachSentence(await screen.findByRole('tooltip'))).toBe(
      'Failed: Duplicate reads 25 above 20',
    );
  });

  it('marks a chip with no report to open the same way', async () => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(evaluation('warn', [metric({ verdict: 'warn' })]), {
          sequencing_qc: { depth: { mean_depth: 18.57 } },
        })}
      />,
    );

    const chip = screen.getByText('Warning 18.6x');
    expect(chip).toHaveClass('table-chip', 'family-qc-chip-static', 'table-chip--warning');
    expect(chip).not.toHaveClass('family-qc-chip--neutral');
    await userEvent.hover(chip);
    expect(breachSentence(await screen.findByRole('tooltip'))).toBe(
      'Warning: Mean depth 18.57 below 20',
    );
  });

  it('names the breach when the chip takes keyboard focus', async () => {
    const user = userEvent.setup();
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(evaluation('fail', [metric({ warn_value: 30, error_value: 20, verdict: 'fail' })]))}
      />,
    );

    await user.tab();
    expect(screen.getByRole('button')).toHaveFocus();
    expect(breachSentence(await screen.findByRole('tooltip'))).toBe(
      'Failed: Mean depth 18.57 below 20',
    );
  });
});
