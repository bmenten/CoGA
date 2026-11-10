// UI telemetry durability: a failed batch, and the events queued before a logout, are not lost.
//
// A Microsoft access token lives about an hour, and the flush at page hide or unload cannot wait
// for a renewal: the flush timer renews the token ahead, and events whose token has expired anyway
// are kept under their account, never sent to be refused, until that account's session can send
// them. Values are made up; tokens are put together here.
//
// The events queued when another request's 401 ended the session are kept under their account,
// and a batch in flight through the API client when the page closes is kept until the API
// acknowledges it; each event carries its own id, by which the API stores it once, so a batch
// sent again is harmless.
//
// A page-close keepalive's events stay kept until the API takes them; each kept event is stored
// on its own, so one tab's write cannot undo another's; and an event no session made is not
// recorded, never sent with the session the next flush finds.

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

  it('settles the flush on logout once the API has answered it, and at once when nothing is queued', async () => {
    const { logUiEvent, flushUiEventsNow } = await load();
    let answer: (response: Response) => void = () => undefined;
    fetchMock.mockImplementationOnce(
      () =>
        new Promise<Response>((resolve) => {
          answer = resolve;
        }),
    );
    logUiEvent({ event_type: 'click', label: 'Logout' });

    let settled = false;
    const flushed = flushUiEventsNow().then(() => {
      settled = true;
    });
    await Promise.resolve();
    expect(settled).toBe(false);

    answer(new Response(null, { status: 202 }));
    await flushed;
    expect(settled).toBe(true);

    // Nothing left to send: settled without a request.
    await flushUiEventsNow();
    expect(fetchMock).toHaveBeenCalledTimes(1);
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

      // The timer renewed the token ahead: the page-close flush sends the renewed one.
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

      // None is sent with the expired token, to be refused.
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
      // Not sent at once with the expired token.
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

    // The refused batch goes out at the account's next sign-in.
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

      // The page-close flush finds no token and sends nothing: the events stay kept.
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

      // The timer's flush finds no token and sends nothing: the events stay kept.
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

      // Only the events queued under this session's account go out under it.
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

      // The flush leaves the rest: the next batch would go out with the token the session holds,
      // the other account's.
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

      // The batch in flight goes out again in the page-close flush.
      expect(fetchMock).toHaveBeenCalledTimes(1);
      const resent = keepaliveEvents(fetchMock, 0);
      expect(resent.map((event) => event.label)).toEqual(['Sign out the analysis', 'Close the tab']);
      // The API stores an event once by its id: one that did arrive is not stored twice.
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
      // Sent again with the id it first went with.
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

  describe('the keepalive as the page is hidden or closed', () => {
    it('that fails while the page is only hidden is sent again by the next flush, with the same ids', async () => {
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      const page = await load();
      stops.push(page.startUiTelemetry());
      page.logUiEvent({ event_type: 'click', label: 'Open the family' });
      fetchMock.mockReturnValueOnce(failedRequest());
      setVisibility('hidden');
      await answersRead();
      await page.flushUiEvents();

      // The keepalive's failure is read: its events are sent again.
      expect(postMock).toHaveBeenCalledTimes(1);
      expect(sentLabels(postMock, 0)).toEqual(['Open the family']);
      // An event that did arrive is stored once, by its id.
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

      // A keepalive that did not arrive keeps its events.
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

      // The refused keepalive's events go out at the account's next sign-in.
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

  describe('the events kept in the browser, with two tabs open', () => {
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

      // Each tab's kept events are sent: neither tab's write undid the other's.
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

      // The sending tab leaves the other tab's kept event in place.
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

  describe('an event made with no session', () => {
    it('on the sign-in page is not sent under the account that signs in there', async () => {
      localStorage.removeItem('token');
      const page = await load();
      page.logUiEvent({ event_type: 'click', label: 'Sign in' });
      persistSession(accessToken(ACCOUNT, Date.now() + HOUR), 'a.colleague@example.org', 'viewer', 'microsoft');
      page.logUiEvent({ event_type: 'click', label: 'Open the dashboard' });
      await page.flushUiEvents();

      // It does not go out with the session the flush finds, which the API would record as that
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

      // The account that signs in next on the page does not send it.
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

      // The refused batch is kept for the account without the event made with no session.
      expect(sentLabels(postMock, 1)).toEqual(['Open the dashboard']);
    });
  });

  // What the page's click and submit listeners record of an action: what was acted on (its
  // kind, its name, its audit ID and a link's target) and where. Only a click on a control
  // or on an element marked for audit is recorded: a plain button's, a link's, a role's, a marked
  // element's and a submit input's click, and a form's submission.
  describe('what a click or a submit records', () => {
    const recorded = async (act: () => void) => {
      const { api, startUiTelemetry, flushUiEvents } = await load();
      stops.push(startUiTelemetry());
      act();
      await flushUiEvents();
      return api.post.mock.calls.length ? sentEvents(api.post, 0) : [];
    };
    const place = (html: string): HTMLElement => {
      const holder = document.createElement('div');
      holder.innerHTML = html;
      document.body.appendChild(holder);
      return holder.firstElementChild as HTMLElement;
    };

    afterEach(() => {
      document.body.innerHTML = '';
      window.history.replaceState(null, '', '/');
    });

    it.each([
      [
        'a link, with where it leads',
        '<a href="/families/F1" id="open-family">Open <b>F1</b></a>',
        { category: 'link', label: 'Open F1', target_id: 'open-family', href: '/families/F1' },
      ],
      [
        'an element by its role, named by its label',
        '<div role="tab" aria-label="Small variants" data-audit-id="tab-small-variants">SV</div>',
        { category: 'tab', label: 'Small variants', target_id: 'tab-small-variants' },
      ],
      [
        'an element marked for audit, as marked',
        '<span data-audit-id="apcad-chart" data-audit-category="chart" data-audit-label="APCAD chart">…</span>',
        { category: 'chart', label: 'APCAD chart', target_id: 'apcad-chart' },
      ],
      ['a submit input, by its value', '<input type="submit" value="Sign out" />', { category: 'submit', label: 'Sign out' }],
      ['a button input, by its value', '<input type="button" value="Retry" />', { category: 'button', label: 'Retry' }],
    ])('records a click on %s, and where it was made', async (_label, html, expected) => {
      window.history.replaceState(null, '', '/families/F1/genome?chrom=7');
      const events = await recorded(() => place(html).click());

      expect(events).toHaveLength(1);
      expect(events[0]).toMatchObject({ event_type: 'click', path: '/families/F1/genome?chrom=7', ...expected });
    });

    it('records a click inside a control as a click on the control', async () => {
      const button = place('<button type="button" id="sign-off">Sign <span>off</span></button>');
      const events = await recorded(() => (button.querySelector('span') as HTMLElement).click());

      expect(events).toEqual([expect.objectContaining({ category: 'button', label: 'Sign off', target_id: 'sign-off' })]);
    });

    it('records no click on what is not a control', async () => {
      const text = place('<p>Family F1 <em>three members</em></p>');
      const events = await recorded(() => {
        text.click();
        (text.querySelector('em') as HTMLElement).click();
        document.body.click();
      });

      expect(events).toEqual([]);
    });

    it('records a form submitted, by its name and ID', async () => {
      window.history.replaceState(null, '', '/families/F1/small-variants');
      const form = place('<form aria-label="Variant filters" data-audit-id="sv-filters"></form>') as HTMLFormElement;
      const events = await recorded(() => form.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true })));

      expect(events).toEqual([
        expect.objectContaining({
          event_type: 'submit',
          category: 'form',
          label: 'Variant filters',
          target_id: 'sv-filters',
          path: '/families/F1/small-variants',
        }),
      ]);
    });
  });
});
