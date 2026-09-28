// UI telemetry durability — #521: a failed batch and the events before logout were lost.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('../api', () => ({ default: { post: vi.fn() }, apiBaseUrl: '/api' }));

const load = async () => {
  vi.resetModules();
  const api = (await import('../api')).default as unknown as { post: ReturnType<typeof vi.fn> };
  const telemetry = await import('../telemetry');
  return { api, ...telemetry };
};

const sentLabels = (post: ReturnType<typeof vi.fn>, call: number) =>
  (post.mock.calls[call][1] as { events: Array<{ label?: string }> }).events.map((e) => e.label);

describe('UI telemetry', () => {
  beforeEach(() => {
    localStorage.setItem('token', 'token-1');
  });

  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('keeps a batch that failed transiently for the next flush', async () => {
    const { api, logUiEvent, flushUiEvents } = await load();
    logUiEvent({ event_type: 'click', label: 'Open family' });
    api.post.mockRejectedValueOnce(new Error('Network Error'));
    await flushUiEvents();
    api.post.mockResolvedValueOnce({ data: {} });
    await flushUiEvents();

    expect(api.post).toHaveBeenCalledTimes(2);
    expect(sentLabels(api.post, 1)).toEqual(['Open family']);
  });

  it('retries a server error but drops a batch the server rejects', async () => {
    const { api, logUiEvent, flushUiEvents } = await load();
    logUiEvent({ event_type: 'click', label: 'A' });
    api.post.mockRejectedValueOnce({ response: { status: 503 } });
    await flushUiEvents();
    api.post.mockRejectedValueOnce({ response: { status: 422 } });
    await flushUiEvents();
    await flushUiEvents();

    // Sent twice (the 503 is retried), then dropped after the 422: nothing is left.
    expect(api.post).toHaveBeenCalledTimes(2);
  });

  it('sends everything queued with the current token when flushed on logout', async () => {
    const fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 204 })));
    vi.stubGlobal('fetch', fetchMock);
    const { logUiEvent, flushUiEventsNow } = await load();
    logUiEvent({ event_type: 'click', label: 'Logout' });
    flushUiEventsNow();

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toBe('/api/ui-events');
    expect(init.keepalive).toBe(true);
    expect((init.headers as Record<string, string>).Authorization).toBe('Bearer token-1');
    expect(JSON.parse(String(init.body)).events[0].label).toBe('Logout');
  });
});
