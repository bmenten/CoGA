import { PREDICTOR_TONE_LABELS, type PredictorTone } from './inSilicoTones';

/** A predictor's value as a variant card or the ACMG dialog lists it, with how it reads. */
export interface PredictorReading {
  label: string;
  value: string;
  // How the value reads, for the mark before it; null for a value with no reading (a gene
  // constraint, a score the annotation does not hold).
  tone: PredictorTone | null;
  // What the thresholds behind the mark are.
  basis?: string;
}

export const SEQR_THRESHOLDS = "seqr's thresholds, from ClinGen's PP3/BP4 calibration";
export const SPLICEAI_THRESHOLDS = 'the SpliceAI cut-offs of the ACMG evaluation';
export const ALPHA_MISSENSE_CLASS_READING = 'its class, as the ACMG evaluation reads it';

/** The coloured mark before a predictor's value, as seqr shows it. */
export default function PredictorMark({ reading }: { reading: PredictorReading }) {
  if (!reading.tone) return null;
  const said = `${reading.label} ${reading.value}: ${PREDICTOR_TONE_LABELS[reading.tone]}`;
  return (
    <span
      className={`variant-card-predictor-mark variant-card-predictor-mark--${reading.tone}`}
      role="img"
      aria-label={PREDICTOR_TONE_LABELS[reading.tone]}
      title={reading.basis ? `${said} (${reading.basis})` : said}
    />
  );
}
