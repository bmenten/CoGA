import { storage } from './storage';

export const defaultGenomeWindow = 500000;
export const defaultChromosomeWindow = 10000;
export const defaultCoverageUpperThreshold = 0.35;
export const defaultCoverageLowerThreshold = -0.35;
export const defaultCoverageRange = 1.5;
const GENOME_KEY = 'genomeWindow';
const CHROM_KEY = 'chromosomeWindow';
const COV_UPPER_KEY = 'coverageUpperThreshold';
const COV_LOWER_KEY = 'coverageLowerThreshold';
const COV_RANGE_KEY = 'coverageRange';

/**
 * Read a numeric setting from storage, falling back to `fallback` when the key
 * is unset or invalid.
 *
 * IMPORTANT: `Number(storage.getItem(key))` is `Number(null) === 0` when the key
 * is absent, and `0` passes `Number.isFinite`. Callers that treated "finite" as
 * "present" therefore silently used `0` for unset settings — which, for the
 * coverage range, collapses the chart's y-scale to a zero-width domain and makes
 * every coverage/segment point render at a non-finite coordinate (i.e. blank).
 * Guard on the raw value first, and require positivity where `0` is meaningless.
 */
function readNumericSetting(
  key: string,
  fallback: number,
  { positive = false }: { positive?: boolean } = {},
): number {
  const raw = storage.getItem(key);
  if (raw === null || raw === '') {
    return fallback;
  }
  const val = Number(raw);
  if (!Number.isFinite(val) || (positive && val <= 0)) {
    return fallback;
  }
  return val;
}

export function getGenomeWindow(): number {
  return readNumericSetting(GENOME_KEY, defaultGenomeWindow, { positive: true });
}

export function getChromosomeWindow(): number {
  return readNumericSetting(CHROM_KEY, defaultChromosomeWindow, { positive: true });
}

export function setGenomeWindow(val: number): void {
  storage.setItem(GENOME_KEY, String(val));
}

export function setChromosomeWindow(val: number): void {
  storage.setItem(CHROM_KEY, String(val));
}

export function getCoverageUpperThreshold(): number {
  // 0 is a legitimate threshold, so only fall back when the key is truly unset.
  return readNumericSetting(COV_UPPER_KEY, defaultCoverageUpperThreshold);
}

export function getCoverageLowerThreshold(): number {
  return readNumericSetting(COV_LOWER_KEY, defaultCoverageLowerThreshold);
}

/**
 * True when this browser colours coverage with its own gain/loss thresholds rather than
 * the defaults. They live in browser storage, so two workstations can colour the same
 * data differently; the chart says so rather than letting it pass unseen (#529).
 */
export function hasCustomCoverageThresholds(): boolean {
  return (
    getCoverageUpperThreshold() !== defaultCoverageUpperThreshold ||
    getCoverageLowerThreshold() !== defaultCoverageLowerThreshold
  );
}

export function resetCoverageThresholds(): void {
  storage.removeItem(COV_UPPER_KEY);
  storage.removeItem(COV_LOWER_KEY);
}

export function setCoverageUpperThreshold(val: number): void {
  storage.setItem(COV_UPPER_KEY, String(val));
}

export function setCoverageLowerThreshold(val: number): void {
  storage.setItem(COV_LOWER_KEY, String(val));
}

export function getCoverageRange(): number {
  // A range of 0 collapses the coverage y-scale and blanks the chart, so an
  // unset/invalid/non-positive value must fall back to the default.
  return readNumericSetting(COV_RANGE_KEY, defaultCoverageRange, { positive: true });
}

export function setCoverageRange(val: number): void {
  storage.setItem(COV_RANGE_KEY, String(val));
}
