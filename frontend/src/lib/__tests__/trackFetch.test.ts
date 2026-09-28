import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiMock = vi.hoisted(() => ({ get: vi.fn() }));
vi.mock('../api', () => ({ default: apiMock }));

import { fetchTrackJson } from '../trackFetch';

const httpError = (status: number) => Object.assign(new Error(`HTTP ${status}`), { response: { status } });

describe('fetchTrackJson (#510)', () => {
  beforeEach(() => {
    apiMock.get.mockReset();
  });

  it('goes through the shared client without prefixing the API base a second time', async () => {
    apiMock.get.mockResolvedValue({ data: { items: [1] } });
    const controller = new AbortController();

    await expect(
      fetchTrackJson('/api/bed/S1/coverage/batch?chrom=1', { signal: controller.signal }),
    ).resolves.toEqual({ items: [1] });
    expect(apiMock.get).toHaveBeenCalledWith('/api/bed/S1/coverage/batch?chrom=1', {
      baseURL: '',
      signal: controller.signal,
    });
  });

  it('reads a "no data" 404 as an empty track only when allowed', async () => {
    apiMock.get.mockImplementation(async () => {
      throw httpError(404);
    });
    await expect(fetchTrackJson('/api/bed/x', { emptyOn404: true })).resolves.toBeNull();
    await expect(fetchTrackJson('/api/bed/x')).rejects.toThrow('HTTP 404');
  });

  it('never turns any other failure into data', async () => {
    apiMock.get.mockImplementation(async () => {
      throw httpError(500);
    });
    await expect(fetchTrackJson('/api/bed/x', { emptyOn404: true })).rejects.toThrow('HTTP 500');

    apiMock.get.mockImplementation(async () => {
      throw new Error('Network Error');
    });
    await expect(fetchTrackJson('/api/bed/x', { emptyOn404: true })).rejects.toThrow('Network Error');
  });
});
