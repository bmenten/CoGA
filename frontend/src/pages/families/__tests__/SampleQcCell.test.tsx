import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, beforeEach, afterEach } from 'vitest';
import SampleQcCell from '../SampleQcCell';
import api from '../../../lib/api';
import type { ApiFamilyRecord } from '../../../lib/apiTypes';
import type {
  SampleSequencingQcEvaluationOut,
  SampleSequencingQcMetricOut,
} from '../../../lib/apiSchema.generated';

vi.mock('../../../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../../../lib/api')>('../../../lib/api');
  return {
    ...actual,
    default: { get: vi.fn(), defaults: { baseURL: '/api' } },
  };
});

const mockedGet = vi.mocked(api.get);

/**
 * A member with its recorded QC and no served verdict (`judged` below adds one). The family
 * read serves none when it has nothing to judge or the evaluation failed, so such a chip
 * reads "Not run".
 */
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
const metric = (overrides: Partial<SampleSequencingQcMetricOut> = {}): SampleSequencingQcMetricOut => ({
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
  verdict: SampleSequencingQcEvaluationOut['verdict'],
  metrics: SampleSequencingQcMetricOut[],
): SampleSequencingQcEvaluationOut => ({
  verdict,
  metrics,
  breached: metrics
    .filter((entry) => entry.verdict === 'warn' || entry.verdict === 'fail')
    .map((entry) => entry.metric_key),
  profile_key: 'default',
  profile_label: 'Default',
});

const judged = (
  verdict: SampleSequencingQcEvaluationOut | null,
  sampleMetadata: Record<string, unknown> = QC,
): ApiFamilyRecord['members'][number] => ({ ...member(sampleMetadata), sequencing_qc: verdict });

/**
 * The breach sentence, or why the sample was not assessed, leads the tooltip; the metrics
 * and the profile follow after a dash.
 */
const breachSentence = (tooltip: HTMLElement): string => (tooltip.textContent ?? '').split(' — ')[0];

const NOT_RUN_NO_LIMIT =
  "Not run: not assessed, because no limit is configured for any of this sample's metrics";
const NOT_RUN_NO_VERDICT = 'Not run: not assessed, because the server returned no QC verdict for this sample';

const TONES = ['family-qc-chip--neutral', 'table-chip--neutral', 'table-chip--warning', 'table-chip--critical'];

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
    // number, after the verdict word (no verdict was served: Not run); N50, quality and
    // yield are on hover.
    expect(screen.getByText('Not run 18.6x')).toBeInTheDocument();
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

    expect(screen.getByText('Not run 30.0x')).toBeInTheDocument();
    // Nothing to open, so the chip must not look like a control.
    expect(screen.queryByRole('button')).not.toBeInTheDocument();
  });

  it('offers the report even when no metrics were parsed', async () => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={member({ sequencing_qc: { report: 'qc/nanoplot/HG002/report.html' } })}
      />,
    );

    // With no metric there is nothing to judge, so no verdict: the chip says Not run, and
    // its tooltip says why and that it opens the report.
    const chip = screen.getByRole('button', { name: 'Not run' });
    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(/^Not run: not assessed, because the server returned no QC verdict/);
    expect(tooltip).toHaveTextContent('Click to open the pipeline QC report in a new tab');
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
    expect(screen.getByText('Not run 9.5x')).toBeInTheDocument();
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

const LOWER: SampleSequencingQcMetricOut['direction'] = 'lower_is_worse';
const HIGHER: SampleSequencingQcMetricOut['direction'] = 'higher_is_worse';

/** A breaching metric per unit, and how the breach sentence writes it against its limit. */
const UNIT_CASES = [
  { unit: 'x', label: 'Mean depth', direction: LOWER, value: 18.57, limit: 20, says: '18.6x below 20x' },
  { unit: '%', label: 'Duplicate reads', direction: HIGHER, value: 14.46, limit: 12, says: '14.5% above 12%' },
  { unit: 'Q', label: 'Median read quality', direction: LOWER, value: 28.4, limit: 30, says: 'Q28 below Q30' },
  { unit: 'bp', label: 'Median read length', direction: LOWER, value: 812.4, limit: 900, says: '812 bp below 900 bp' },
  { unit: 'reads', label: 'Read count', direction: LOWER, value: 640, limit: 750, says: '640 reads below 750 reads' },
  { unit: 'fraction', label: 'Fraction bases aligned', direction: LOWER, value: 0.91234, limit: 0.95, says: '0.912 below 0.95' },
  // The limit is never rounded.
  { unit: 'x', label: 'Mean depth', direction: LOWER, value: 18.57, limit: 19.25, says: '18.6x below 19.25x' },
  // Where the rounding would put the value on its limit, it keeps the decimals that show the
  // breach: never "20.0x below 20x".
  { unit: 'x', label: 'Mean depth', direction: LOWER, value: 19.96, limit: 20, says: '19.96x below 20x' },
  { unit: '%', label: 'Duplicate reads', direction: HIGHER, value: 12.04, limit: 12, says: '12.04% above 12%' },
  { unit: 'Q', label: 'Median read quality', direction: LOWER, value: 29.6, limit: 30, says: 'Q29.6 below Q30' },
  // A unit the chip does not know: the value as served, with its unit.
  { unit: 'kb', label: 'Some metric', direction: LOWER, value: 4.567, limit: 5, says: '4.567 kb below 5 kb' },
];

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
      lead: null,
    },
    {
      state: 'warn',
      verdict: evaluation('warn', [metric({ warn_value: 20, error_value: 10, verdict: 'warn' })]),
      name: 'Warning 18.6x',
      tone: 'table-chip--warning',
      lead: 'Warning: Mean depth 18.6x below 20x',
    },
    {
      state: 'fail',
      verdict: evaluation('fail', [metric({ warn_value: 30, error_value: 20, verdict: 'fail' })]),
      name: 'Fail 18.6x',
      tone: 'table-chip--critical',
      lead: 'Failed: Mean depth 18.6x below 20x',
    },
    {
      state: 'not run (no limit configured)',
      verdict: evaluation('skip', [metric({ warn_value: null, error_value: null, verdict: 'skip' })]),
      name: 'Not run 18.6x',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_LIMIT,
    },
    {
      state: 'not run (no verdict served)',
      verdict: null,
      name: 'Not run 18.6x',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_VERDICT,
    },
  ])('$state: the chip reads "$name" in tone $tone', async ({ verdict, name, tone, lead }) => {
    render(<SampleQcCell familyId="pacbio" member={judged(verdict)} />);

    const chip = screen.getByRole('button');
    // Every state but a pass is a word on the chip, so none rests on colour alone, and a
    // sample that was not assessed never reads as a pass; a passing chip carries the
    // number only.
    expect(chip).toHaveAccessibleName(name);
    expect(chip).toHaveClass('table-chip', 'family-qc-chip', tone);
    for (const other of TONES.filter((candidate) => candidate !== tone)) {
      expect(chip).not.toHaveClass(other);
    }

    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    if (lead) {
      // A warning is named against its warning limit, a failure against its error limit;
      // a sample that was not assessed is said to be, with the reason.
      expect(breachSentence(tooltip)).toBe(lead);
    } else {
      expect(tooltip).not.toHaveTextContent(/Warning:|Failed:|Not run|not assessed/);
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
      'Failed: Mean depth 8.2x below 10x; Duplicate reads 14.5% above 12%',
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
      'Failed: Duplicate reads 25.0% above 20%',
    );
  });

  it.each([
    {
      state: 'pass',
      verdict: evaluation('pass', [metric({ warn_value: 15, error_value: 10, verdict: 'pass' })]),
      text: '18.6x',
      tone: 'family-qc-chip--neutral',
      lead: null,
    },
    {
      state: 'warn',
      verdict: evaluation('warn', [metric({ verdict: 'warn' })]),
      text: 'Warning 18.6x',
      tone: 'table-chip--warning',
      lead: 'Warning: Mean depth 18.6x below 20x',
    },
    {
      state: 'not run (no limit configured)',
      verdict: evaluation('skip', [metric({ warn_value: null, error_value: null, verdict: 'skip' })]),
      text: 'Not run 18.6x',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_LIMIT,
    },
    {
      state: 'not run (no verdict served)',
      verdict: null,
      text: 'Not run 18.6x',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_VERDICT,
    },
  ])('$state: a chip with no report to open reads "$text" too', async ({ verdict, text, tone, lead }) => {
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(verdict, { sequencing_qc: { depth: { mean_depth: 18.57 } } })}
      />,
    );

    const chip = screen.getByText(text);
    expect(chip).toHaveClass('table-chip', 'family-qc-chip-static', tone);
    for (const other of TONES.filter((candidate) => candidate !== tone)) {
      expect(chip).not.toHaveClass(other);
    }
    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    if (lead) {
      expect(breachSentence(tooltip)).toBe(lead);
    } else {
      expect(tooltip).not.toHaveTextContent(/Warning:|Failed:|Not run|not assessed/);
    }
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
      'Failed: Mean depth 18.6x below 20x',
    );
  });

  // ENG-10: without a report the chip took no keyboard focus, so its tooltip was mouse-only.
  it.each([
    {
      state: 'fail',
      verdict: evaluation('fail', [metric({ warn_value: 30, error_value: 20, verdict: 'fail' })]),
      text: 'Fail 18.6x',
      lead: 'Failed: Mean depth 18.6x below 20x',
    },
    { state: 'not run', verdict: null, text: 'Not run 18.6x', lead: NOT_RUN_NO_VERDICT },
  ])(
    '$state: a chip with no report to open takes keyboard focus, which shows its tooltip',
    async ({ verdict, text, lead }) => {
      const user = userEvent.setup();
      render(
        <SampleQcCell
          familyId="pacbio"
          member={judged(verdict, { sequencing_qc: { depth: { mean_depth: 18.57 } } })}
        />,
      );

      await user.tab();
      const chip = screen.getByText(text);
      expect(chip).toHaveFocus();
      const tooltip = await screen.findByRole('tooltip');
      expect(breachSentence(tooltip)).toBe(lead);
      // A screen reader reads the tooltip as the chip's description. The chip is still no
      // button: there is nothing to open.
      expect(chip).toHaveAccessibleDescription(tooltip.textContent ?? '');
      expect(screen.queryByRole('button')).not.toBeInTheDocument();

      await user.tab();
      expect(chip).not.toHaveFocus();
      expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    },
  );

  // ENG-10: the sentence gave the value as served, without its unit ("18.57" beside a chip
  // reading 18.6x). It now writes the value as the chip and the metric list round it, and
  // the limit exactly as configured, both in the metric's unit.
  it.each(UNIT_CASES)(
    '$label in $unit: "$says"',
    async ({ unit, label, direction, value, limit, says }) => {
      const breached = metric({ label, unit, direction, value, warn_value: limit, error_value: null, verdict: 'warn' });
      render(<SampleQcCell familyId="pacbio" member={judged(evaluation('warn', [breached]))} />);

      await userEvent.hover(screen.getByRole('button'));
      expect(breachSentence(await screen.findByRole('tooltip'))).toBe(`Warning: ${label} ${says}`);
    },
  );
});

/**
 * An embryo that appears only in the PGT pipeline's family tables: the QC import records its
 * `pgt` entry alone (allele drop-out and drop-in, Mendelian concordance before and after
 * imputation, as percentages), so it has no depth, N50 or report to show.
 */
const EMBRYO_PGT = {
  allele_dropout_rate: 14.2,
  allele_dropin_rate: 2.1,
  mendelian_concordance: 41.3,
  mendelian_concordance_imputed: 96.5,
};

/** The embryo metrics as the server serves them: in its catalogue's order, in %. */
const ADO = {
  metric_key: 'pgt.allele_dropout_rate',
  label: 'Allele drop-out (ADO)',
  unit: '%',
  direction: 'higher_is_worse',
  value: EMBRYO_PGT.allele_dropout_rate,
} as const;
const ADI = {
  metric_key: 'pgt.allele_dropin_rate',
  label: 'Allele drop-in (ADI)',
  unit: '%',
  direction: 'higher_is_worse',
  value: EMBRYO_PGT.allele_dropin_rate,
} as const;
const CONCORDANCE = {
  metric_key: 'pgt.mendelian_concordance',
  label: 'Mendelian concordance before imputation',
  unit: '%',
  direction: 'lower_is_worse',
  value: EMBRYO_PGT.mendelian_concordance,
} as const;
const IMPUTED_CONCORDANCE = {
  metric_key: 'pgt.mendelian_concordance_imputed',
  label: 'Mendelian concordance after imputation',
  unit: '%',
  direction: 'lower_is_worse',
  value: EMBRYO_PGT.mendelian_concordance_imputed,
} as const;

type Limits = Pick<SampleSequencingQcMetricOut, 'warn_value' | 'error_value' | 'verdict'>;
const NO_LIMIT: Limits = { warn_value: null, error_value: null, verdict: 'skip' };
const ADI_PASS: Limits = { warn_value: 5, error_value: 10, verdict: 'pass' };
const IMPUTED_PASS: Limits = { warn_value: 95, error_value: 90, verdict: 'pass' };

/** The four embryo metrics, each with its limits and its own verdict. */
const embryoMetrics = (
  ado: Limits,
  adi: Limits = NO_LIMIT,
  concordance: Limits = NO_LIMIT,
  imputed: Limits = NO_LIMIT,
): SampleSequencingQcMetricOut[] => [
  metric({ ...ADO, ...ado }),
  metric({ ...ADI, ...adi }),
  metric({ ...CONCORDANCE, ...concordance }),
  metric({ ...IMPUTED_CONCORDANCE, ...imputed }),
];

const embryo = (
  verdict: SampleSequencingQcEvaluationOut | null,
  pgt: Record<string, number> = EMBRYO_PGT,
  extra: Record<string, unknown> = {},
): ApiFamilyRecord['members'][number] => ({
  ...judged(verdict, { sequencing_qc: { ...extra, pgt } }),
  sample_id: 'EMB1',
  role: 'embryo',
});

// CLIN-30: a sample whose recorded QC has no depth, N50 or report showed "-" even when the
// server served a verdict, a fail included (REQ-QC-005). The chip now shows the verdict
// with the value of the metric that decided it; where no metric decided it, the verdict
// alone; the reason is in the tooltip, as on a chip with a depth.
describe('SampleQcCell with a verdict but no depth, N50 or report', () => {
  beforeEach(() => {
    mockedGet.mockReset();
    vi.spyOn(window, 'open').mockImplementation(() => null);
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it.each([
    {
      state: 'fail',
      verdict: evaluation(
        'fail',
        embryoMetrics({ warn_value: 8, error_value: 12, verdict: 'fail' }, ADI_PASS, NO_LIMIT, IMPUTED_PASS),
      ),
      text: 'Fail ADO 14.2%',
      tone: 'table-chip--critical',
      lead: 'Failed: Allele drop-out (ADO) 14.2% above 12%',
    },
    {
      state: 'warn',
      verdict: evaluation(
        'warn',
        embryoMetrics({ warn_value: 12, error_value: 20, verdict: 'warn' }, ADI_PASS, NO_LIMIT, IMPUTED_PASS),
      ),
      text: 'Warning ADO 14.2%',
      tone: 'table-chip--warning',
      lead: 'Warning: Allele drop-out (ADO) 14.2% above 12%',
    },
    {
      state: 'pass',
      verdict: evaluation(
        'pass',
        embryoMetrics({ warn_value: 20, error_value: 30, verdict: 'pass' }, ADI_PASS, NO_LIMIT, IMPUTED_PASS),
      ),
      text: 'ADO 14.2%',
      tone: 'family-qc-chip--neutral',
      lead: null,
    },
    {
      state: 'not run (no limit configured)',
      verdict: evaluation('skip', embryoMetrics(NO_LIMIT)),
      text: 'Not run',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_LIMIT,
    },
    {
      state: 'not run (no verdict served)',
      verdict: null,
      text: 'Not run',
      tone: 'table-chip--neutral',
      lead: NOT_RUN_NO_VERDICT,
    },
  ])('$state: an embryo with only its PGT metrics reads "$text"', async ({ verdict, text, tone, lead }) => {
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict)} />);

    expect(screen.queryByText('-')).not.toBeInTheDocument();
    const chip = screen.getByText(text);
    // Nothing to open, so a static chip, in the tone of its verdict.
    expect(chip).toHaveClass('table-chip', 'family-qc-chip-static', tone);
    for (const other of TONES.filter((candidate) => candidate !== tone)) {
      expect(chip).not.toHaveClass(other);
    }
    expect(screen.queryByRole('button')).not.toBeInTheDocument();

    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    if (lead) {
      expect(breachSentence(tooltip)).toBe(lead);
    } else {
      expect(tooltip).not.toHaveTextContent(/Warning:|Failed:|Not run|not assessed/);
    }
    // Every embryo metric is listed, in its unit.
    expect(tooltip).toHaveTextContent(
      'ADO 14.2% · ADI 2.1% · Mendelian concordance 41.3% before, 96.5% after imputation',
    );
  });

  it("shows the metric whose verdict is the sample's, not an earlier one with a milder verdict", async () => {
    const verdict = evaluation(
      'fail',
      embryoMetrics(
        { warn_value: 12, error_value: 20, verdict: 'warn' },
        ADI_PASS,
        NO_LIMIT,
        { warn_value: 98, error_value: 97, verdict: 'fail' },
      ),
    );
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict)} />);

    const chip = screen.getByText('Fail Imputed concordance 96.5%');
    await userEvent.hover(chip);
    // The tooltip names every breach, the warning included.
    expect(breachSentence(await screen.findByRole('tooltip'))).toBe(
      'Failed: Allele drop-out (ADO) 14.2% above 12%; Mendelian concordance after imputation 96.5% below 97%',
    );
  });

  it.each([
    {
      state: 'of two failing metrics, the first in the order the server lists them',
      verdict: evaluation(
        'fail',
        embryoMetrics(
          { warn_value: 8, error_value: 12, verdict: 'fail' },
          { warn_value: 1, error_value: 2, verdict: 'fail' },
        ),
      ),
      text: 'Fail ADO 14.2%',
    },
    {
      state: 'a pass judged on a later metric only: that metric',
      verdict: evaluation('pass', embryoMetrics(NO_LIMIT, NO_LIMIT, NO_LIMIT, IMPUTED_PASS)),
      text: 'Imputed concordance 96.5%',
    },
    {
      state: 'a warning on the concordance before imputation',
      verdict: evaluation(
        'warn',
        embryoMetrics(NO_LIMIT, NO_LIMIT, { warn_value: 50, error_value: 20, verdict: 'warn' }),
      ),
      text: 'Warning Concordance 41.3%',
    },
    {
      state: 'a fail on the allele drop-in',
      verdict: evaluation('fail', embryoMetrics(NO_LIMIT, { warn_value: 1, error_value: 2, verdict: 'fail' })),
      text: 'Fail ADI 2.1%',
    },
  ])('$state: "$text"', ({ verdict, text }) => {
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict)} />);

    expect(screen.getByText(text)).toBeInTheDocument();
  });

  it('keeps the decimals that show the breach on the chip, as in the tooltip', async () => {
    const pgt = { ...EMBRYO_PGT, allele_dropout_rate: 12.04 };
    const verdict = evaluation('fail', [
      metric({ ...ADO, value: 12.04, warn_value: 8, error_value: 12, verdict: 'fail' }),
    ]);
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict, pgt)} />);

    // Rounded to one decimal it would read 12.0%, on the limit it crossed.
    const chip = screen.getByText('Fail ADO 12.04%');
    await userEvent.hover(chip);
    expect(breachSentence(await screen.findByRole('tooltip'))).toBe(
      'Failed: Allele drop-out (ADO) 12.04% above 12%',
    );
  });

  it('rounds a passing value as the metric list does: no limit was crossed to stay past', () => {
    const pgt = { ...EMBRYO_PGT, allele_dropout_rate: 4.123 };
    const verdict = evaluation('pass', [
      metric({ ...ADO, value: 4.123, warn_value: 20, error_value: 30, verdict: 'pass' }),
    ]);
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict, pgt)} />);

    expect(screen.getByText('ADO 4.1%')).toBeInTheDocument();
  });

  it('names a metric the chip has no short name for by the label the server serves', () => {
    const verdict = evaluation('fail', [
      metric({ ...DUPLICATES, value: 25, warn_value: 12, error_value: 20, verdict: 'fail' }),
    ]);
    render(
      <SampleQcCell
        familyId="pacbio"
        member={judged(verdict, { sequencing_qc: { alignment: { duplicated_reads_percent: 25 } } })}
      />,
    );

    expect(screen.getByText('Fail Duplicate reads 25.0%')).toBeInTheDocument();
  });

  it('opens the report from such a chip when the sample has one', async () => {
    mockedGet.mockResolvedValue({ data: { url: '/families/PGT01/qc-report/EMB1?token=abc' } });
    const user = userEvent.setup();
    const verdict = evaluation('fail', embryoMetrics({ warn_value: 8, error_value: 12, verdict: 'fail' }, ADI_PASS));
    render(
      <SampleQcCell
        familyId="PGT01"
        member={embryo(verdict, EMBRYO_PGT, { report: 'qualimap/EMB1/qualimapReport.html' })}
      />,
    );

    const chip = screen.getByRole('button', { name: 'Fail ADO 14.2%' });
    expect(chip).toHaveClass('table-chip', 'family-qc-chip', 'table-chip--critical');
    await user.click(chip);
    await waitFor(() => expect(mockedGet).toHaveBeenCalledWith('/families/PGT01/qc-report/EMB1/link'));
    await waitFor(() =>
      expect(window.open).toHaveBeenCalledWith(
        '/api/families/PGT01/qc-report/EMB1?token=abc',
        '_blank',
        'noopener,noreferrer',
      ),
    );
  });

  it('names the breach when the chip takes keyboard focus', async () => {
    const user = userEvent.setup();
    const verdict = evaluation('fail', embryoMetrics({ warn_value: 8, error_value: 12, verdict: 'fail' }));
    render(<SampleQcCell familyId="PGT01" member={embryo(verdict)} />);

    await user.tab();
    const chip = screen.getByText('Fail ADO 14.2%');
    expect(chip).toHaveFocus();
    const tooltip = await screen.findByRole('tooltip');
    expect(breachSentence(tooltip)).toBe('Failed: Allele drop-out (ADO) 14.2% above 12%');
    expect(chip).toHaveAccessibleDescription(tooltip.textContent ?? '');
  });

  it.each([
    { state: 'pass', text: 'Pass', tone: 'family-qc-chip--neutral' },
    { state: 'warn', text: 'Warning', tone: 'table-chip--warning' },
    { state: 'fail', text: 'Fail', tone: 'table-chip--critical' },
  ] as const)('$state with no metric to show: the verdict alone, "$text", never "-"', ({ state, text, tone }) => {
    // The member came with no recorded QC, and the verdict names no metric.
    render(<SampleQcCell familyId="PGT01" member={judged(evaluation(state, []), {})} />);

    expect(screen.queryByText('-')).not.toBeInTheDocument();
    expect(screen.getByText(text)).toHaveClass('table-chip', 'family-qc-chip-static', tone);
  });

  it('reads Not run for recorded QC the server judged nothing of, and "-" only for none', async () => {
    const { unmount } = render(
      <SampleQcCell
        familyId="PGT01"
        member={judged(null, {
          sequencing_qc: { sex_check: { method: 'ngs-bits SampleGender', inferred_sex: 'female' } },
        })}
      />,
    );

    const chip = screen.getByText('Not run');
    await userEvent.hover(chip);
    const tooltip = await screen.findByRole('tooltip');
    expect(breachSentence(tooltip)).toBe(NOT_RUN_NO_VERDICT);
    expect(tooltip).toHaveTextContent('sex read as female (ngs-bits SampleGender)');
    unmount();

    // Nothing the chip can show: no verdict, and no number, recorded metric or report.
    render(
      <SampleQcCell
        familyId="PGT01"
        member={judged(null, { sequencing_qc: { reads: { quality_cutoffs: { q20: { reads: 1 } } } } })}
      />,
    );
    expect(screen.getByText('-')).toBeInTheDocument();
  });
});
