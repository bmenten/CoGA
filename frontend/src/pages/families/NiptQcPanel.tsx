import React from 'react';
import type { ApiNiptFetalFraction, QcStatus } from '../../lib/apiTypes';
import type { NiptQcOut } from '../../lib/apiSchema.generated';
import { QC_STATUS_CHIP, QC_STATUS_LABEL } from '../../lib/qcStatus';
import { depth, pct } from './niptClassification';

// Why cfDNA calls failed the quality filter (nipt_analysis.quality_failures).
export const NIPT_QUALITY_FAILURE_LABELS: Record<string, string> = {
  low_quality: 'quality (TLOD) below the minimum',
  few_alt_reads: 'too few alt reads',
  low_vaf: 'allele fraction below 1%',
  strand_bias: 'strand bias',
  below_fetal_vaf_floor: 'allele fraction below a quarter of FF/2',
};

const FETAL_SEX_COPY: Record<string, string> = {
  female: 'Female',
  male: 'Male',
  indeterminate: 'Indeterminate',
  discordant: 'Discordant',
};

const CHRY_PROFILE_COPY: Record<string, string> = {
  female_no_chrY_signal: 'no chrY signal',
  female_with_male_fetal_signal: 'male fetal chrY signal',
  high_chrY_review: 'chrY near the autosomal level',
  male_like_not_maternal_plasma: 'male DNA',
  no_chrY_targets: 'no chrY target in the panel',
};

const fetalSexStatus = (call: string): QcStatus => {
  if (call === 'discordant') return 'fail';
  if (call === 'indeterminate') return 'warn';
  return 'pass';
};

const StatusChip: React.FC<{ status: QcStatus }> = ({ status }) => (
  <span className={QC_STATUS_CHIP[status]}>{QC_STATUS_LABEL[status]}</span>
);

const Row: React.FC<{ status?: QcStatus; title: React.ReactNode; children?: React.ReactNode }> = ({
  status,
  title,
  children,
}) => (
  <div className="qc-check-row">
    {status ? <StatusChip status={status} /> : <span className="table-chip table-chip--neutral">Info</span>}
    <div className="qc-check-body">
      <p className="qc-check-title">{title}</p>
      {children}
    </div>
  </div>
);

const share = (part: number, whole: number): string =>
  whole ? `${((part / whole) * 100).toFixed(1)}%` : '—';

interface NiptQcPanelProps {
  qc: NiptQcOut;
  fetalFraction: ApiNiptFetalFraction;
}

/**
 * The NIPT quality checks a reader confirms before reading any variant: the fetal
 * fraction, the fetal sex from two independent signals, paternity, the plasma sample's
 * sex profile, the capture targets' coverage and why cfDNA calls failed the quality
 * filter.
 */
const NiptQcPanel: React.FC<NiptQcPanelProps> = ({ qc, fetalFraction: ff }) => {
  const paternity = qc.paternity;
  const sex = qc.fetal_sex;
  const coverage = qc.target_coverage;
  const failures = Object.entries(qc.quality_failures || {}).filter(([, count]) => count > 0);
  return (
    <section className="surface-card space-y-2" aria-label="NIPT quality checks">
      <h2 className="section-title">Quality checks</h2>
      <Row
        status={ff.ff_computed == null ? 'fail' : ff.low_confidence ? 'warn' : 'pass'}
        title={
          ff.ff_computed == null
            ? 'Fetal fraction: not estimated'
            : `Fetal fraction ${pct(ff.ff_computed)}${
                ff.ci_low != null && ff.ci_high != null ? ` (95% CI ${pct(ff.ci_low)}–${pct(ff.ci_high)})` : ''
              }`
        }
      >
        <p className="table-subtle">
          From {ff.n_sites.toLocaleString()} paternal alleles the fetus inherited (the mother has none); per-site
          median {pct(ff.ff_median)}.
          {sex.chry_fetal_fraction != null
            ? ` chrY coverage suggests about ${pct(sex.chry_fetal_fraction)} (indicative: one chrY target).`
            : ''}
          {qc.de_novo_window
            ? ` Fetal window for de novo alleles ${pct(qc.de_novo_window.strict_min)}–${pct(
                qc.de_novo_window.strict_max,
              )} (loose ${pct(qc.de_novo_window.loose_min)}–${pct(qc.de_novo_window.loose_max)}).`
            : ''}
          {ff.low_confidence ? ' Low confidence: too few sites or a wide interval.' : ''}
        </p>
      </Row>
      <Row status={fetalSexStatus(sex.call)} title={`Fetal sex: ${FETAL_SEX_COPY[sex.call] ?? sex.call}`}>
        <p className="table-subtle">
          Paternal X: {sex.x_transmitted} of the father&apos;s X alleles seen, {sex.x_not_transmitted} absent (
          {sex.paternal_x}). chrY coverage:{' '}
          {sex.chry_profile ? (CHRY_PROFILE_COPY[sex.chry_profile] ?? sex.chry_profile) : 'no target table'}
          {sex.y_ratio != null ? ` (chrY/autosomes ${sex.y_ratio.toFixed(3)})` : ''}.
          {sex.call === 'discordant' ? ' The two signals disagree: check the samples.' : ''}
        </p>
      </Row>
      <Row status={paternity.status} title="Paternity">
        <p className="table-subtle">
          {paternity.hom_alt_transmitted + paternity.hom_alt_not_transmitted > 0
            ? `Homozygous paternal alleles seen ${paternity.hom_alt_transmitted} of ${
                paternity.hom_alt_transmitted + paternity.hom_alt_not_transmitted
              } (${pct(paternity.hom_alt_rate)}; all expected). `
            : ''}
          {paternity.het_transmitted + paternity.het_not_transmitted > 0
            ? `Het paternal alleles seen ${paternity.het_transmitted} of ${
                paternity.het_transmitted + paternity.het_not_transmitted
              } (${pct(paternity.het_rate)}; half expected). `
            : ''}
          {paternity.message}
        </p>
      </Row>
      <Row
        status={qc.plasma_profile_status === 'unknown' ? 'skip' : (qc.plasma_profile_status as QcStatus)}
        title="Maternal plasma sample"
      >
        <p className="table-subtle">
          {qc.plasma_profile_message}
          {sex.x_ratio != null ? ` chrX/autosomes ${sex.x_ratio.toFixed(2)}.` : ''}
        </p>
      </Row>
      {coverage ? (
        <Row title={`Target coverage: median ${depth(coverage.median_mean)} over ${coverage.targets.toLocaleString()} targets`}>
          <p className="table-subtle">
            {coverage.below_critical.toLocaleString()} ({share(coverage.below_critical, coverage.targets)}) below{' '}
            {depth(coverage.critical_mean_depth)}, {coverage.below_advisory.toLocaleString()} below{' '}
            {depth(coverage.advisory_mean_depth)}, {coverage.incomplete.toLocaleString()} with uncovered bases,{' '}
            {coverage.zero_mean.toLocaleString()} without coverage. The coverage card below lists the weak targets
            of the selected genes.
          </p>
        </Row>
      ) : null}
      <Row title="cfDNA quality filter">
        <p className="table-subtle">
          {failures.length
            ? `Calls left out: ${failures
                .map(([reason, count]) => `${count.toLocaleString()} ${NIPT_QUALITY_FAILURE_LABELS[reason] ?? reason}`)
                .join('; ')}. `
            : 'No cfDNA call failed the quality filter. '}
          Model: {qc.model.reference}; quality {qc.model.min_quality} or more, {qc.model.min_alt_reads}+ alt reads,
          allele fraction {pct(qc.model.min_vaf)}+ and {qc.model.vaf_ff_fraction} × FF/2, strand bias FS ≤{' '}
          {qc.model.max_strand_bias_fs}.
        </p>
      </Row>
    </section>
  );
};

export default NiptQcPanel;
