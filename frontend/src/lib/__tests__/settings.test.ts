import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { storage } from '../storage';
import {
  defaultCoverageLowerThreshold,
  defaultCoverageRange,
  defaultCoverageUpperThreshold,
  defaultChromosomeWindow,
  defaultGenomeWindow,
  getChromosomeWindow,
  getCoverageLowerThreshold,
  getCoverageRange,
  getCoverageUpperThreshold,
  getGenomeWindow,
} from '../settings';

describe('coverage/window settings fallbacks', () => {
  beforeEach(() => storage.clear());
  afterEach(() => storage.clear());

  // Number(storage.getItem(key)) === Number(null) === 0, and 0 is finite. An unset
  // coverageRange read that way would resolve to 0, collapsing the coverage chart's
  // y-scale and blanking coverage + segments entirely.
  it('returns the default coverage range when the key is unset (not 0)', () => {
    expect(getCoverageRange()).toBe(defaultCoverageRange);
    expect(getCoverageRange()).toBeGreaterThan(0);
  });

  it('falls back to the default range for non-positive or invalid values', () => {
    storage.setItem('coverageRange', '0');
    expect(getCoverageRange()).toBe(defaultCoverageRange);
    storage.setItem('coverageRange', '-2');
    expect(getCoverageRange()).toBe(defaultCoverageRange);
    storage.setItem('coverageRange', 'not-a-number');
    expect(getCoverageRange()).toBe(defaultCoverageRange);
  });

  it('honours a valid stored coverage range', () => {
    storage.setItem('coverageRange', '2.5');
    expect(getCoverageRange()).toBe(2.5);
  });

  it('returns threshold defaults when unset', () => {
    expect(getCoverageUpperThreshold()).toBe(defaultCoverageUpperThreshold);
    expect(getCoverageLowerThreshold()).toBe(defaultCoverageLowerThreshold);
  });

  it('allows 0 as an explicit threshold value', () => {
    storage.setItem('coverageUpperThreshold', '0');
    expect(getCoverageUpperThreshold()).toBe(0);
  });

  it('returns window defaults when unset and ignores non-positive values', () => {
    expect(getGenomeWindow()).toBe(defaultGenomeWindow);
    expect(getChromosomeWindow()).toBe(defaultChromosomeWindow);
    storage.setItem('genomeWindow', '0');
    expect(getGenomeWindow()).toBe(defaultGenomeWindow);
  });
});
