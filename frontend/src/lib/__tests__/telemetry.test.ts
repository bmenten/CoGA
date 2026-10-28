// UI telemetry durability — #521: a failed batch and the events before logout were lost.
//
// A page closed after an idle hour sent its last events with the session's stored token (found
// while fixing SEC-32, #1005): a Microsoft access token lives about an hour, and only a request
// through the API client renewed it, so the flush at page hide or unload, which cannot wait for a
// renewal, was refused (401) and its events lost. The flush timer now renews the token ahead, and
// events whose token has expired anyway are kept under their account, never sent to be refused,
// until that account's session can send them. Values are made up; tokens are put together here.
//
// Two older losses (found while fixing the keepalive flush, #1018): the events queued when
// another request's 401 ended the session were dropped, as no token was left to send them with,
// and a batch in flight through the API client when the page closed was in neither the queue nor
// the page-close flush. Both are now kept, the batch until the API acknowledges it; each event
// carries its own id, by which the API stores it once (SEC-17), so a batch sent again is harmless.
//
// Three more (SEC-36): nothing read the page-close keepalive's answer, so one that failed lost
// its events; tabs wrote the kept events as one value, so one tab's write could undo another's;
// and an event made with no session went out with the session the next flush found, recorded as
// that user's. A keepalive's events now stay kept until the API takes them, each kept event is
// stored on its own, and an event no session made is not recorded.

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAuthToken, persistSession } from '../auth';

const { postMock, freshMicrosoftAccessTokenMock } = vi.hoisted(() => ({
  postMock: vi.fn(),
  freshMicrosoftAccessTokenMock: vi.fn(),
}));

// The API client's axios instance is replaced; its sessionAccessToken stays real, and asks MSAL
// (below) for a Microsoft session's token, as the API client and IGV do.
vi.mock('../api', async () => {
  const actual = await vi.importActual<typeof import('../api')>('../api');
  return { ...actual, default: { post: postMock }, apiBaseUrl: '/api' };
});
vi.mock('../microsoftSignIn', () => ({ freshMicrosoftAccessToken: freshMicrosoftAccessTokenMock }));

const MINUTE = 60 * 1000;
const HOUR = 60 * MINUTE;
const NOW = Date.parse('2026-10-27T09:00:00Z');
const ACCOUNT = '6f1c2d3e-0000-4000-8000-00000000c0a1';
const OTHER_ACCOUNT = '6f1c2d3e-0000-4000-8000-00000000c0a2';

const encoded = (part: object): string =>
  btoa(JSON.stringify(part)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

// An access token as Microsoft issues one for CoGA's API: the account's object ID, and when it
// expires. Made up at run time; nothing checks its signature here.
const accessToken = (account: string, expiresAt: number): string =>
  [
    encoded({ alg: 'RS256', typ: 'JWT' }),
    encoded({ oid: account, exp: Math.floor(expiresAt / 1000) }),
    ['made', 'up'].join('-'),
  ].join('.');

const load = async () => {
  vi.resetModules();
  const client = await import('../api');
  const api = client.default as unknown as { post: ReturnType<typeof vi.fn> };
  const telemetry = await import('../telemetry');
  return { api, endExpiredSession: client.endExpiredSession, ...telemetry };
};

type SentEvent = { label?: string; event_id?: string };

const sentEvents = (post: ReturnType<typeof vi.fn>, call: number): SentEvent[] =>
  (post.mock.calls[call][1] as { events: SentEvent[] }).events;

const sentLabels = (post: ReturnType<typeof vi.fn>, call: number) => sentEvents(post, call).map((e) => e.label);

const keepaliveEvents = (fetchMock: ReturnType<typeof vi.fn>, call: number): SentEvent[] =>
  (JSON.parse(String((fetchMock.mock.calls[call][1] as RequestInit).body)) as { events: SentEvent[] }).events;

const keepaliveHeaders = (fetchMock: ReturnType<typeof vi.fn>) =>
  fetchMock.mock.calls.map(([, init]) => (init as RequestInit).headers as Record<string, string>);

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

// The page another request's 401 sends to sign in (the API client's endExpiredSession): it does
// not navigate here.
const familyPage = () => ({ pathname: '/families/F1', search: '', hash: '', replace: vi.fn() });

const setVisibility = (state: DocumentVisibilityState): void => {
  Object.defineProperty(document, 'visibilityState', { configurable: true, get: () => state });
  document.dispatchEvent(new Event('visibilitychange'));
};

// A keepalive that does not arrive (the network failed). The test reads the failure too, so a
// page that ignores it raises no unhandled rejection.
const failedRequest = (): Promise<Response> => {
  const failed = Promise.reject(new TypeError('Failed to fetch'));
  failed.catch(() => {});
  return failed;
};

// Once the page has read the answers already given.
const answersRead = (): Promise<void> => new Promise((resolve) => setTimeout(resolve, 0));

// Two tabs run at once: `otherTab` runs between this tab's read of the kept events and its first
// write of them, as the other tab's write can land there.
const interleave = (otherTab: () => void): void => {
  const setItem = localStorage.setItem.bind(localStorage);
  const removeItem = localStorage.removeItem.bind(localStorage);
  let done = false;
  const between = (key: string): void => {
    if (done || !key.startsWith('coga.ui-events.kept')) return;
    done = true;
    otherTab();
  };
  vi.spyOn(localStorage, 'setItem').mockImplementation((key: string, value: string) => {
    between(key);
    setItem(key, value);
  });
  vi.spyOn(localStorage, 'removeItem').mockImplementation((key: string) => {
    between(key);
    removeItem(key);
  });
};

const allSentLabels = (post: ReturnType<typeof vi.fn>) =>
  post.mock.calls.flatMap((_, call) => sentLabels(post, call));

describe('UI telemetry', () => {
  const stops: Array<() => void> = [];
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    localStorage.setItem('token', 'token-1');
    postMock.mockReset();
    postMock.mockResolvedValue({ data: {} });
    freshMicrosoftAccessTokenMock.mockReset();
    fetchMock = vi.fn(() => Promise.resolve(new Response(null, { status: 202 })));
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    stops.splice(0).forEach((stop) => stop());
    Reflect.deleteProperty(document, 'visibilityState');
    localStorage.clear();
    vi.unstubAllGlobals();
    vi.useRealTimers();
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

  describe('a Microsoft session’s token at page close', () => {
    it('is renewed ahead by the flush timer, so the last events of a page closed after an idle hour go out', async () => {
      vi.useFakeTimers({ now: NOW });
      // The token the session got with its last request; MSAL renews it when asked.
      const held = accessToken(ACCOUNT, NOW + 10 * MINUTE);
      const renewed = accessToken(ACCOUNT, NOW + 70 * MINUTE);
      persistSession(held, 'a.colleague@example.org', 'viewer', 'microsoft');
      freshMicrosoftAccessTokenMock.mockResolvedValue(renewed);
      const { logUiEvent, startUiTelemetry } = await load();
      stops.push(startUiTelemetry());

      // An idle hour: nothing queued, no request made.
      await vi.advanceTimersByTimeAsync(HOUR);
      logUiEvent({ event_type: 'click', label: 'Close the family' });
      window.dispatchEvent(new Event('pagehide'));

      // Before, the timer flushed only queued events and renewed nothing: the page sent the
      // token it held, an hour old, and the API refused the batch.
      expect(keepaliveHeaders(fetchMock).map((headers) => headers.Authorization)).toEqual([`Bearer ${renewed}`]);
      expect(getAuthToken()).toBe(renewed);
      // Renewed once, five minutes before it expired, not at every tick.
      expect(freshMicrosoftAccessTokenMock).toHaveBeenCalledTimes(1);
    });

    it('when it has expired anyway, is not sent: the events are kept for the account’s next sign-in', async () => {
      vi.useFakeTimers({ now: NOW });
      // The device slept past the token's expiry; MSAL cannot renew it without the user.
      persistSession(accessToken(ACCOUNT, NOW - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      freshMicrosoftAccessTokenMock.mockResolvedValue(null);
      const closing = await load();
      stops.push(closing.startUiTelemetry());
      closing.logUiEvent({ event_type: 'click', label: 'Open the report' });
      window.dispatchEvent(new Event('pagehide'));

      // Before, they were sent with the expired token, refused, and lost.
      expect(fetchMock).not.toHaveBeenCalled();

      // Another account signs in at this browser: it sends none of them.
      const other = await load();
      persistSession(accessToken(OTHER_ACCOUNT, NOW + HOUR), 'other@example.org', 'viewer', 'microsoft');
      await other.flushUiEvents();
      expect(postMock).not.toHaveBeenCalled();

      // The next page load once the account has signed in again sends them, once.
      const next = await load();
      persistSession(accessToken(ACCOUNT, NOW + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      await next.flushUiEvents();
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the report']);
    });

    it('when it has expired at logout, is not sent either: the logout click is kept for the account', async () => {
      vi.useFakeTimers({ now: NOW });
      persistSession(accessToken(ACCOUNT, NOW - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      const leaving = await load();
      leaving.logUiEvent({ event_type: 'click', label: 'Logout' });
      leaving.flushUiEventsNow();
      expect(fetchMock).not.toHaveBeenCalled();

      const next = await load();
      persistSession(accessToken(ACCOUNT, NOW + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      expect(sentLabels(postMock, 0)).toEqual(['Logout']);
    });

    it('when the page is only hidden with an expired token, the events wait for the renewed one', async () => {
      vi.useFakeTimers({ now: NOW });
      const renewed = accessToken(ACCOUNT, NOW + HOUR);
      persistSession(accessToken(ACCOUNT, NOW - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      freshMicrosoftAccessTokenMock.mockResolvedValue(renewed);
      const { logUiEvent, startUiTelemetry } = await load();
      stops.push(startUiTelemetry());
      logUiEvent({ event_type: 'click', label: 'Compare the samples' });

      setVisibility('hidden');
      // Before, sent at once with the expired token, and lost.
      expect(fetchMock).not.toHaveBeenCalled();

      // The renewal the hidden page started has finished by the timer's next flush.
      await vi.advanceTimersByTimeAsync(5000);
      expect(getAuthToken()).toBe(renewed);
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Compare the samples']);
    });

    it('is asked of MSAL again only a minute after a renewal that brought no token', async () => {
      vi.useFakeTimers({ now: NOW });
      persistSession(accessToken(ACCOUNT, NOW + 2 * MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      freshMicrosoftAccessTokenMock.mockResolvedValue(null);
      const { startUiTelemetry } = await load();
      stops.push(startUiTelemetry());

      await vi.advanceTimersByTimeAsync(55 * 1000);
      expect(freshMicrosoftAccessTokenMock).toHaveBeenCalledTimes(1);
      await vi.advanceTimersByTimeAsync(10 * 1000);
      expect(freshMicrosoftAccessTokenMock).toHaveBeenCalledTimes(2);
    });
  });

  it('never asks MSAL for a password session’s token', async () => {
    vi.useFakeTimers({ now: NOW });
    persistSession(accessToken(ACCOUNT, NOW + MINUTE), 'a.colleague@example.org', 'viewer', 'password');
    const { startUiTelemetry } = await load();
    stops.push(startUiTelemetry());

    await vi.advanceTimersByTimeAsync(2 * MINUTE);
    expect(freshMicrosoftAccessTokenMock).not.toHaveBeenCalled();
  });

  it('keeps a batch the API refuses for its session (401) for the account’s next sign-in, instead of dropping it', async () => {
    persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
    const { logUiEvent, flushUiEvents } = await load();
    logUiEvent({ event_type: 'click', label: 'Save the review' });
    logUiEvent({ event_type: 'click', label: 'Sign out' });
    postMock.mockRejectedValueOnce({ response: { status: 401 } });
    await flushUiEvents();

    // The API client ended the session; the account signs in again.
    persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
    await flushUiEvents();

    // Before, the refused batch was dropped and nothing was sent again.
    expect(postMock).toHaveBeenCalledTimes(2);
    expect(sentLabels(postMock, 1)).toEqual(['Save the review', 'Sign out']);
  });

  it('gives every event an id of its own, which it is sent with', async () => {
    const { logUiEvent, flushUiEvents } = await load();
    logUiEvent({ event_type: 'click', label: 'A' });
    logUiEvent({ event_type: 'click', label: 'B' });
    await flushUiEvents();

    const ids = sentEvents(postMock, 0).map((event) => event.event_id);
    expect(ids).toHaveLength(2);
    ids.forEach((id) => expect(id).toMatch(UUID));
    expect(new Set(ids).size).toBe(2);
    // The account an event was queued under stays in the browser.
    expect(Object.keys(sentEvents(postMock, 0)[0])).not.toContain('account');
  });

  it('gives an event a UUID also where the browser has no crypto.randomUUID (a page not served over HTTPS)', async () => {
    vi.stubGlobal('crypto', { getRandomValues: crypto.getRandomValues.bind(crypto) });
    const { logUiEvent, flushUiEvents } = await load();
    logUiEvent({ event_type: 'click', label: 'A' });
    await flushUiEvents();

    // A version-4 UUID, as the API reads one.
    expect(sentEvents(postMock, 0)[0].event_id).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/,
    );
  });

  describe('events queued when another request’s 401 ends the session', () => {
    it('are kept for the account they were queued for, not dropped, and go out at its next sign-in', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Open the family' });
      page.logUiEvent({ event_type: 'click', label: 'Show the variants' });
      // The variants request is refused (401): the API client ends the session, and the page
      // closes on its way to sign in.
      page.endExpiredSession(familyPage());
      window.dispatchEvent(new Event('pagehide'));

      // Before, the page-close flush found no token and emptied the queue.
      expect(fetchMock).not.toHaveBeenCalled();

      // Another account signs in at this browser: it sends none of them.
      const other = await load();
      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + HOUR), 'other@example.org', 'viewer', 'microsoft');
      await other.flushUiEvents();
      expect(postMock).not.toHaveBeenCalled();

      // The account signs in again: they go out once.
      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      await next.flushUiEvents();
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the family', 'Show the variants']);
    });

    it('are kept also when the flush timer finds the session ended before the page closes', async () => {
      vi.useFakeTimers({ now: NOW });
      persistSession(accessToken(ACCOUNT, NOW + HOUR), 'a.colleague@example.org', 'viewer', 'password');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Open the report' });
      page.endExpiredSession(familyPage());

      // Before, the timer's flush found no token and emptied the queue.
      await vi.advanceTimersByTimeAsync(5000);
      window.dispatchEvent(new Event('pagehide'));
      expect(postMock).not.toHaveBeenCalled();
      expect(fetchMock).not.toHaveBeenCalled();

      const next = await load();
      persistSession(accessToken(ACCOUNT, NOW + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'password');
      await next.flushUiEvents();
      expect(sentLabels(postMock, 0)).toEqual(['Open the report']);
    });

    it('are never sent under the account that signs in next on the same page', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      page.logUiEvent({ event_type: 'click', label: 'Open the family' });
      page.endExpiredSession(familyPage());

      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + HOUR), 'other@example.org', 'viewer', 'microsoft');
      page.logUiEvent({ event_type: 'click', label: 'Open the dashboard' });
      await page.flushUiEvents();

      // Before, the queue went out whole under the session the flush found.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the dashboard']);

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      expect(sentLabels(postMock, 1)).toEqual(['Open the family']);
    });

    it('while a batch is in flight leave the rest to the next flush, which sends none of it under the next account', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      for (let i = 0; i < 51; i += 1) page.logUiEvent({ event_type: 'click', label: `Event ${i}` });
      let acknowledge: (value: unknown) => void = () => {};
      postMock.mockReturnValueOnce(new Promise((resolve) => (acknowledge = resolve)));
      const flushing = page.flushUiEvents();
      // The session ends meanwhile, and another account signs in on the page.
      page.endExpiredSession(familyPage());
      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + HOUR), 'other@example.org', 'viewer', 'microsoft');
      acknowledge({ data: { accepted: 50 } });
      await flushing;
      await page.flushUiEvents();

      // Before, the flush went on with the next batch, which the API client sends with the token
      // the session holds: the other account's.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toHaveLength(50);

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      expect(sentLabels(postMock, 1)).toEqual(['Event 50']);
    });
  });

  describe('a batch in flight through the API client', () => {
    it('stays queued until the API acknowledges it: a page closed mid-flight sends it again, with the same ids', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Sign out the analysis' });
      // The page closes before the API answers.
      postMock.mockReturnValueOnce(new Promise(() => {}));
      void page.flushUiEvents();
      page.logUiEvent({ event_type: 'click', label: 'Close the tab' });
      window.dispatchEvent(new Event('pagehide'));

      // Before, the batch in flight was in neither the queue nor the page-close flush.
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const resent = keepaliveEvents(fetchMock, 0);
      expect(resent.map((event) => event.label)).toEqual(['Sign out the analysis', 'Close the tab']);
      // The API stores an event once by its id (SEC-17): one that did arrive is not stored twice.
      expect(resent[0].event_id).toBe(sentEvents(postMock, 0)[0].event_id);
      expect(resent[0].event_id).toMatch(UUID);
    });

    it('is kept for the account when the session ends before the API answers and the page closes', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Save the review' });
      postMock.mockReturnValueOnce(new Promise(() => {}));
      void page.flushUiEvents();
      // Another request is refused (401) meanwhile: the session ends, and the page closes.
      page.endExpiredSession(familyPage());
      window.dispatchEvent(new Event('pagehide'));
      expect(fetchMock).not.toHaveBeenCalled();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      // Before, it was lost. Sent again with the id it first went with.
      expect(postMock).toHaveBeenCalledTimes(2);
      expect(sentLabels(postMock, 1)).toEqual(['Save the review']);
      expect(sentEvents(postMock, 1)[0].event_id).toBe(sentEvents(postMock, 0)[0].event_id);
    });

    it('sent again as the page is only hidden, is not sent a third time once the API acknowledges it', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Compare the samples' });
      let acknowledge: (value: unknown) => void = () => {};
      postMock.mockReturnValueOnce(new Promise((resolve) => (acknowledge = resolve)));
      const flushing = page.flushUiEvents();
      setVisibility('hidden');
      acknowledge({ data: { accepted: 1 } });
      await flushing;
      await page.flushUiEvents();
      window.dispatchEvent(new Event('pagehide'));

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(keepaliveEvents(fetchMock, 0)[0].event_id).toBe(sentEvents(postMock, 0)[0].event_id);
      expect(postMock).toHaveBeenCalledTimes(1);
    });

    it('once acknowledged, is not sent again', async () => {
      const { logUiEvent, flushUiEvents, flushUiEventsNow } = await load();
      logUiEvent({ event_type: 'click', label: 'Open family' });
      await flushUiEvents();
      flushUiEventsNow();

      expect(postMock).toHaveBeenCalledTimes(1);
      expect(fetchMock).not.toHaveBeenCalled();
    });
  });

  describe('the keepalive as the page is hidden or closed (SEC-36)', () => {
    it('that fails while the page is only hidden is sent again by the next flush, with the same ids', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Open the family' });
      fetchMock.mockReturnValueOnce(failedRequest());
      setVisibility('hidden');
      await answersRead();
      await page.flushUiEvents();

      // Before, nothing read the keepalive's answer: its events had left the queue, and were lost.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the family']);
      // An event that did arrive is stored once, by its id (SEC-17).
      expect(sentEvents(postMock, 0)[0].event_id).toBe(keepaliveEvents(fetchMock, 0)[0].event_id);
    });

    it('whose answer the closed page never read is sent again with the account’s next flush', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const closing = await load();
      stops.push(closing.startUiTelemetry());
      closing.logUiEvent({ event_type: 'click', label: 'Sign out the analysis' });
      // The page is gone before the API answers.
      fetchMock.mockReturnValueOnce(new Promise(() => {}));
      window.dispatchEvent(new Event('pagehide'));
      expect(fetchMock).toHaveBeenCalledTimes(1);

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      // Before, a keepalive that did not arrive lost its events.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Sign out the analysis']);
      expect(sentEvents(postMock, 0)[0].event_id).toBe(keepaliveEvents(fetchMock, 0)[0].event_id);
    });

    it('that the API refuses for its session (401) is kept for the account’s next sign-in', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Save the review' });
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 401 }));
      window.dispatchEvent(new Event('pagehide'));
      await answersRead();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      // Before, the refused keepalive's events were lost.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Save the review']);
    });

    it('that the API takes is not sent again, nor by a flush while it is on its way', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Compare the samples' });
      let answer: (response: Response) => void = () => {};
      fetchMock.mockReturnValueOnce(new Promise<Response>((resolve) => (answer = resolve)));
      setVisibility('hidden');
      await page.flushUiEvents();
      answer(new Response(null, { status: 202 }));
      await answersRead();
      await page.flushUiEvents();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      expect(fetchMock).toHaveBeenCalledTimes(1);
      expect(postMock).not.toHaveBeenCalled();
    });
  });

  describe('the events kept in the browser, with two tabs open (SEC-36)', () => {
    it('lose none when both tabs keep theirs at the same moment', async () => {
      // The device slept past the token's expiry, and both tabs are closed.
      persistSession(accessToken(ACCOUNT, Date.now() - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      const tabA = await load();
      const tabB = await load();
      tabA.logUiEvent({ event_type: 'click', label: 'Open the family' });
      tabB.logUiEvent({ event_type: 'click', label: 'Open the report' });
      interleave(() => tabB.flushUiEventsNow());
      tabA.flushUiEventsNow();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      // Before, the tab that wrote last wrote the kept events as it had read them, without the
      // other tab's.
      expect([...allSentLabels(postMock)].sort()).toEqual(['Open the family', 'Open the report']);
    });

    it('lose none another tab keeps while one tab sends the account’s kept events', async () => {
      // A tab still holds an event of another account that was signed in at this browser.
      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + HOUR), 'other@example.org', 'viewer', 'microsoft');
      const tabB = await load();
      tabB.logUiEvent({ event_type: 'click', label: 'Open the report' });
      // An event kept for the account from an earlier page, whose token had expired.
      persistSession(accessToken(ACCOUNT, Date.now() - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      const earlier = await load();
      earlier.logUiEvent({ event_type: 'click', label: 'Open the family' });
      earlier.flushUiEventsNow();

      // The account signs in again in one tab, whose flush sends its kept event, as the other
      // tab is closed and keeps the other account's.
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const tabA = await load();
      interleave(() => tabB.flushUiEventsNow());
      await tabA.flushUiEvents();
      expect(sentLabels(postMock, 0)).toEqual(['Open the family']);

      const next = await load();
      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + 2 * HOUR), 'other@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      // Before, the sending tab wrote back the kept events as it had read them, without the
      // other tab's.
      expect(postMock).toHaveBeenCalledTimes(2);
      expect(sentLabels(postMock, 1)).toEqual(['Open the report']);
    });

    it('are at most 200 per account: the oldest go first', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() - MINUTE), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      for (let i = 0; i < 150; i += 1) page.logUiEvent({ event_type: 'click', label: `Event ${i}` });
      page.flushUiEventsNow();
      for (let i = 150; i < 250; i += 1) page.logUiEvent({ event_type: 'click', label: `Event ${i}` });
      page.flushUiEventsNow();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      expect(allSentLabels(postMock)).toEqual(Array.from({ length: 200 }, (_, i) => `Event ${i + 50}`));
    });
  });

  describe('an event made with no session (SEC-36)', () => {
    it('on the sign-in page is not sent under the account that signs in there', async () => {
      localStorage.removeItem('token');
      const page = await load();
      page.logUiEvent({ event_type: 'click', label: 'Sign in' });
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      page.logUiEvent({ event_type: 'click', label: 'Open the dashboard' });
      await page.flushUiEvents();

      // Before, it went out with the session the flush found, and the API recorded it as that
      // user's: it takes the actor from the token.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the dashboard']);
    });

    it('after another request’s 401 ended the session is sent under no account', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      page.endExpiredSession(familyPage());
      page.logUiEvent({ event_type: 'click', label: 'Open the dashboard' });
      persistSession(accessToken(OTHER_ACCOUNT, Date.now() + HOUR), 'other@example.org', 'viewer', 'microsoft');
      await page.flushUiEvents();

      // Before, the account that signed in next on the page sent it.
      expect(postMock).not.toHaveBeenCalled();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();
      expect(postMock).not.toHaveBeenCalled();
    });

    it('is not kept for the account whose batch the API refuses (401)', async () => {
      localStorage.removeItem('token');
      const page = await load();
      page.logUiEvent({ event_type: 'click', label: 'Sign in' });
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      page.logUiEvent({ event_type: 'click', label: 'Open the dashboard' });
      postMock.mockRejectedValueOnce({ response: { status: 401 } });
      await page.flushUiEvents();

      const next = await load();
      persistSession(accessToken(ACCOUNT, Date.now() + 2 * HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      await next.flushUiEvents();

      // Before, the refused batch was kept, the event made with no session in it, for the account.
      expect(sentLabels(postMock, 1)).toEqual(['Open the dashboard']);
    });
  });
});
