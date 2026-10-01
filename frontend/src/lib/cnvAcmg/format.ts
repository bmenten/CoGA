// How a ClinGen CNV point value is written: the classification modal's criterion ranges and
// the scale bar's total use this one format.

/**
 * A ClinGen CNV point value, signed and to two decimals: +0.90, +0.99, 0.00, -0.45, +1.00.
 * The class thresholds (0.99, 0.90, -0.90, -0.99) differ in the second decimal and a total is
 * rounded to it, so every value carries both decimals: "+0.9" beside "+0.99" reads as a
 * value of another precision. A value that rounds to zero is "0.00", never "-0.00".
 * The small-variant ACMG points are whole numbers and keep their own format (AcmgScaleBar).
 */
export const formatCnvPoints = (points: number): string => {
  const fixed = points.toFixed(2);
  if (Number(fixed) === 0) return '0.00';
  return points > 0 ? `+${fixed}` : fixed;
};
