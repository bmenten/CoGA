// The running build (TF-15 §1, TF-18 §2) — REQ-UI-008 (risks H9, H10): read from /api/version, shown in the app footer
// and on every report footer.
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { fetchAppVersion, formatBuild, formatSoftwareVersion } from '../appVersion';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));

vi.mock('../api', () => ({ default: apiMock }));

describe('formatBuild / formatSoftwareVersion', () => {
  it('name the version and the short git SHA', () => {
    expect(formatBuild('0.1.0-beta.1', '0123456789ab')).toBe('0.1.0-beta.1 (0123456)');
    expect(formatSoftwareVersion('0.1.0-beta.1', '0123456789ab')).toBe('CoGA 0.1.0-beta.1 (0123456)');
  });

  it('leave out a git SHA the build did not record', () => {
    expect(formatSoftwareVersion('0.0.0+unknown', 'unknown')).toBe('CoGA 0.0.0+unknown');
    expect(formatSoftwareVersion('0.1.0', '')).toBe('CoGA 0.1.0');
    expect(formatSoftwareVersion('0.1.0', null)).toBe('CoGA 0.1.0');
  });
});

describe('fetchAppVersion', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it('reads the running build from /api/version', async () => {
    apiMock.get.mockResolvedValue({ data: { version: '0.1.0', git_sha: '0123456789ab' } });

    await expect(fetchAppVersion()).resolves.toEqual({ version: '0.1.0', git_sha: '0123456789ab' });
    expect(apiMock.get).toHaveBeenCalledWith('/version');
  });

  it('fails on a response that names no version, instead of showing none', async () => {
    for (const data of [{}, [], null, { version: '  ', git_sha: 'abc' }, { version: '0.1.0' }]) {
      apiMock.get.mockResolvedValue({ data });
      await expect(fetchAppVersion()).rejects.toThrow(/version/i);
    }
  });
});
