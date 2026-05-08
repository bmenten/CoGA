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

export function getGenomeWindow(): number {
  const val = Number(storage.getItem(GENOME_KEY));
  return Number.isFinite(val) && val > 0 ? val : defaultGenomeWindow;
}

export function getChromosomeWindow(): number {
  const val = Number(storage.getItem(CHROM_KEY));
  return Number.isFinite(val) && val > 0 ? val : defaultChromosomeWindow;
}

export function setGenomeWindow(val: number): void {
  storage.setItem(GENOME_KEY, String(val));
}

export function setChromosomeWindow(val: number): void {
  storage.setItem(CHROM_KEY, String(val));
}

export function getCoverageUpperThreshold(): number {
  const val = Number(storage.getItem(COV_UPPER_KEY));
  return Number.isFinite(val) ? val : defaultCoverageUpperThreshold;
}

export function getCoverageLowerThreshold(): number {
  const val = Number(storage.getItem(COV_LOWER_KEY));
  return Number.isFinite(val) ? val : defaultCoverageLowerThreshold;
}

export function setCoverageUpperThreshold(val: number): void {
  storage.setItem(COV_UPPER_KEY, String(val));
}

export function setCoverageLowerThreshold(val: number): void {
  storage.setItem(COV_LOWER_KEY, String(val));
}

export function getCoverageRange(): number {
  const val = Number(storage.getItem(COV_RANGE_KEY));
  return Number.isFinite(val) ? val : defaultCoverageRange;
}

export function setCoverageRange(val: number): void {
  storage.setItem(COV_RANGE_KEY, String(val));
}
