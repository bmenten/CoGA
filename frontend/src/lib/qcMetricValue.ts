/**
 * How a sequencing-QC metric's value is written, per unit, wherever it is shown: the members
 * table's QC chip and its tooltip, and a signed version's sequencing QC. One writer, so a
 * value reads the same in both ("18.6x"); the signed report wrote the raw value ("18.57 x")
 * where the chip wrote "18.6x" (ENG-10).
 */

/** The fields of a metric the value is written from: as served, and as a signed record keeps it. */
export interface QcMetricValue {
  value: number;
  unit: string;
  /** `warn` or `fail` for a metric outside a cut-off. */
  verdict: string;
  warn_value?: number | null;
  error_value?: number | null;
  /** `lower_is_worse` or `higher_is_worse`; inferred from the value and the limit when absent. */
  direction?: string | null;
}

/**
 * How a value is written in a unit: "18.6x", "6.4%", "Q32", "14,283 bp". `digits` is the
 * default rounding; a unit not listed here is written as served, with its unit after it.
 */
interface UnitFormat {
  digits?: number;
  write: (value: number, digits: number) => string;
}

const grouped = (value: number, digits: number): string =>
  value.toLocaleString(undefined, { minimumFractionDigits: digits, maximumFractionDigits: digits });

const UNIT_FORMAT: Record<string, UnitFormat> = {
  x: { digits: 1, write: (value, digits) => `${value.toFixed(digits)}x` },
  '%': { digits: 1, write: (value, digits) => `${value.toFixed(digits)}%` },
  Q: { digits: 0, write: (value, digits) => `Q${value.toFixed(digits)}` },
  bp: { digits: 0, write: (value, digits) => `${grouped(value, digits)} bp` },
  reads: { digits: 0, write: (value, digits) => `${grouped(value, digits)} reads` },
  fraction: { digits: 3, write: (value, digits) => value.toFixed(digits) },
};

const unitFormat = (unit: string): UnitFormat =>
  UNIT_FORMAT[unit] ?? { write: (value, digits) => `${value.toFixed(digits)} ${unit}`.trim() };

/** The decimals a number is written with ("19.96" → 2), so it can be shown as it is. */
const decimalsOf = (value: number): number => {
  const [mantissa, exponent = '0'] = String(value).split('e');
  const decimals = (mantissa.split('.')[1] ?? '').length - Number(exponent);
  return Math.min(20, Math.max(0, decimals));
};

const isBreach = (metric: QcMetricValue): boolean => metric.verdict === 'warn' || metric.verdict === 'fail';

/** The limit a breaching metric crossed: its error limit for a fail, its warning limit otherwise. */
export const crossedLimit = (metric: QcMetricValue): number | null | undefined =>
  metric.verdict === 'fail' ? metric.error_value : metric.warn_value;

/** Whether a lower value is the worse one: as served, or, without it, the side of the limit
 *  the breaching value lies on. */
export const lowerIsWorse = (metric: QcMetricValue): boolean => {
  if (metric.direction) return metric.direction === 'lower_is_worse';
  const bound = crossedLimit(metric);
  return typeof bound === 'number' ? metric.value < bound : true;
};

/**
 * A metric's value in its unit, rounded as the metric list rounds it, unless the metric
 * breached and that rounding would put it on or past the limit it crossed (19.96 against 20
 * is not "20.0x below 20x"): then it gets the decimals it needs to stay on the failing side.
 */
export const formatQcMetricValue = (metric: QcMetricValue): string => {
  const format = unitFormat(metric.unit);
  const exact = decimalsOf(metric.value);
  let digits = format.digits ?? exact;
  const bound = crossedLimit(metric);
  if (isBreach(metric) && typeof bound === 'number') {
    const lower = lowerIsWorse(metric);
    while (digits < exact) {
      const shown = Number(metric.value.toFixed(digits));
      if (lower ? shown < bound : shown > bound) break;
      digits += 1;
    }
  }
  return format.write(metric.value, digits);
};

/** A limit in a metric's unit, written exactly as configured: "20x", "30%". */
export const formatQcLimit = (unit: string, limit: number): string => unitFormat(unit).write(limit, decimalsOf(limit));
