// Client-side UI interaction telemetry.
//
// Captures interactive-element clicks (buttons, links, tabs, menu items, form
// submits) and in-app navigations, batches them, and POSTs them to
// `/ui-events` for storage. This complements the backend request audit log,
// which already records every HTTP call: these are the interactions that never
// reach the backend. Identifier masking (paths -> :id, query strings -> keys)
// happens server-side; the client only sends element labels and structure.
import api, { apiBaseUrl, sessionAccessToken } from './api';
import { getAuthToken, getSessionSignIn } from './auth';
import { storage } from './storage';

type UiEventType = 'click' | 'navigation' | 'submit' | 'view' | 'query';

export type UiEventInput = {
  event_type: UiEventType;
  category?: string;
  label?: string;
  target_id?: string;
  path?: string;
  to_path?: string;
  href?: string;
  component?: string;
  detail?: Record<string, unknown>;
};

type QueuedUiEvent = UiEventInput & {
  // The event's own id: the API stores an event once by it (SEC-17), so a batch sent again (one
  // in flight as the page closed) stores nothing twice.
  event_id: string;
  session_id: string;
  occurred_at: string;
};

// A queued event, the account of the session it was queued under (tokenAccount; null for a token
// that names none), and its place among the events its page made, which orders the events kept
// in the browser that were made in the same millisecond. Both stay in the browser: the API takes
// the actor from the token.
type PendingUiEvent = { event: QueuedUiEvent; account: string | null; seq: number };

const MAX_QUEUE = 200;
const MAX_BATCH = 50;
// Keepalive flushes the whole queue at once; chunk it so no single POST exceeds
// the backend per-batch cap (_MAX_EVENTS_PER_BATCH = 100), which now rejects
// oversize batches with 422 instead of silently dropping the overflow.
const MAX_KEEPALIVE_BATCH = 100;
const FLUSH_INTERVAL_MS = 5000;
const MAX_LABEL_LEN = 120;
// A Microsoft access token lives about an hour, and only a request renews it (MSAL, through
// sessionAccessToken). The flush at page hide or unload sends the token the session holds and
// cannot wait for a renewal, so the flush timer renews it this long before it expires, also
// with nothing queued: a page closed after an idle hour sent its last events with an expired
// token, and the API refused them (found while fixing SEC-32).
const RENEW_AHEAD_MS = 5 * 60 * 1000;
// After a renewal that brought no new token (MSAL needs the user), the next is asked a minute
// later rather than at every flush.
const RENEW_RETRY_MS = 60 * 1000;
// A token this close to its expiry may be refused by the time a flush reaches the API.
const EXPIRY_MARGIN_MS = 30 * 1000;
// Events the API would refuse for their session's token wait here, under their account, for a
// flush of that account the API takes (its token renewed, or the account signed in again): in
// the browser's storage, so that they outlive the page, and never sent under another account.
// Each is kept under a key of its own, this prefix and its event_id: tabs share the storage, and
// one value that each tab read, changed and wrote back lost what another tab wrote in between
// (SEC-36).
const KEPT_EVENT_KEY_PREFIX = 'coga.ui-events.kept.';

// Interactive elements we treat as meaningful clicks. `data-audit-id` lets a
// component opt a custom element in and give it a stable identifier.
const INTERACTIVE_SELECTOR =
  'button, a[href], [role="button"], [role="tab"], [role="menuitem"], ' +
  'input[type="submit"], input[type="button"], summary, [data-audit-id]';

let queue: PendingUiEvent[] = [];
let eventsMade = 0;
let sessionId = '';
let started = false;
let flushTimer: ReturnType<typeof setInterval> | null = null;
let flushInFlight = false;
let renewalDueAt = 0;
// The events of the page-close keepalives still on their way: kept until the API takes them, and
// not sent again by a flush meanwhile.
const onTheirWay = new Set<string>();

const getSessionId = (): string => {
  if (!sessionId) {
    try {
      sessionId = crypto.randomUUID();
    } catch {
      sessionId = `s-${Date.now().toString(36)}-${crypto.getRandomValues(new Uint32Array(1))[0].toString(36)}`;
    }
  }
  return sessionId;
};

// A random UUID (version 4), made from the browser's random values where it has no
// crypto.randomUUID (a page not served over HTTPS or from localhost).
const newEventId = (): string => {
  try {
    return crypto.randomUUID();
  } catch {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, '0')).join('');
    return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join('-');
  }
};

const nowIso = (): string => new Date().toISOString();

const truncate = (value: string | null | undefined, max: number): string | undefined => {
  if (!value) return undefined;
  const collapsed = value.replace(/\s+/g, ' ').trim();
  return collapsed ? collapsed.slice(0, max) : undefined;
};

export const logUiEvent = (event: UiEventInput): void => {
  const token = getAuthToken();
  // An event no session made (on the sign-in page, or once the session has ended) is not
  // recorded: the API records every event of a batch as the act of the user whose token sends
  // it, and no user's session made this one. Before, it went out with the session the next flush
  // found, if any, as that user's (SEC-36).
  if (!token) return;
  if (queue.length >= MAX_QUEUE) {
    queue.shift();
  }
  eventsMade += 1;
  queue.push({
    event: {
      ...event,
      label: truncate(event.label, MAX_LABEL_LEN),
      event_id: newEventId(),
      session_id: getSessionId(),
      occurred_at: nowIso(),
    },
    account: tokenAccount(token),
    seq: eventsMade,
  });
};

// A failure worth retrying on the next flush: no response (network) or a server-side
// error. A 4xx will not succeed on retry, so that batch is dropped, but for a refused
// session (401, below).
const isTransientFailure = (error: unknown): boolean => {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return status === undefined || status === 429 || status >= 500;
};

// The API refused the session's token: expired and not renewed (MSAL needs the user), or of an
// account that is no longer active. The API client then ends the session.
const isRefusedSession = (error: unknown): boolean =>
  (error as { response?: { status?: number } })?.response?.status === 401;

type TokenClaims = { exp?: unknown; oid?: unknown; sub?: unknown };

// A bearer token's claims, read without checking it (the API checks it); null for a token that
// is no JWT.
const tokenClaims = (token: string): TokenClaims | null => {
  const payload = token.split('.')[1];
  if (!payload) return null;
  try {
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const binary = atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='));
    const claims: unknown = JSON.parse(
      new TextDecoder().decode(Uint8Array.from(binary, (char) => char.charCodeAt(0))),
    );
    return claims !== null && typeof claims === 'object' ? (claims as TokenClaims) : null;
  } catch {
    return null;
  }
};

// When the token expires, in milliseconds; null when it does not say.
const tokenExpiresAt = (token: string): number | null => {
  const exp = tokenClaims(token)?.exp;
  return typeof exp === 'number' && Number.isFinite(exp) ? exp * 1000 : null;
};

// The account a token signs in: a Microsoft token's object ID, which stays when the account's
// address changes (SEC-32), else its subject; null when it names none.
const tokenAccount = (token: string): string | null => {
  const claims = tokenClaims(token);
  const account = claims?.oid ?? claims?.sub;
  return typeof account === 'string' && account ? account : null;
};

// Whether the API would refuse the token for its age by the time a flush reaches it.
const hasExpired = (token: string): boolean => {
  const expiresAt = tokenExpiresAt(token);
  return expiresAt !== null && expiresAt - EXPIRY_MARGIN_MS <= Date.now();
};

type KeptUiEvent = { account: string; event: QueuedUiEvent; seq: number };

const keptKey = (eventId: string): string => `${KEPT_EVENT_KEY_PREFIX}${eventId}`;

const isKeptUiEvent = (value: unknown): value is KeptUiEvent => {
  const kept = value as KeptUiEvent;
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof kept.account === 'string' &&
    typeof kept.seq === 'number' &&
    typeof kept.event === 'object' &&
    kept.event !== null &&
    typeof kept.event.event_id === 'string' &&
    typeof kept.event.occurred_at === 'string'
  );
};

// The order the events were made in: by their time, then by their place on the page that made
// them.
const byWhenMade = (a: KeptUiEvent, b: KeptUiEvent): number => {
  if (a.event.occurred_at !== b.event.occurred_at) return a.event.occurred_at < b.event.occurred_at ? -1 : 1;
  return a.seq - b.seq;
};

// Every event kept in the browser, any account's, oldest first. An entry that cannot be read is
// removed: no event can be sent from it.
const readKept = (): KeptUiEvent[] => {
  const keys: string[] = [];
  for (let index = 0; index < storage.length; index += 1) {
    const key = storage.key(index);
    if (key?.startsWith(KEPT_EVENT_KEY_PREFIX)) keys.push(key);
  }
  const kept: KeptUiEvent[] = [];
  for (const key of keys) {
    try {
      const value: unknown = JSON.parse(storage.getItem(key) ?? 'null');
      if (isKeptUiEvent(value) && key === keptKey(value.event.event_id)) {
        kept.push(value);
        continue;
      }
    } catch {
      // Not JSON: removed below.
    }
    storage.removeItem(key);
  }
  return kept.sort(byWhenMade);
};

// Keep events under their account instead of sending them to be refused, or dropping them: the
// UI-event log is part of the audit, and its rows are kept for good (#893). Each event is written
// on its own, so tabs keeping or sending events at the same moment leave each other's in place;
// at worst two tabs both send an event, which the API stores once, by its event_id (SEC-17).
// Bounded by MAX_QUEUE per account, as the queue is: beyond it, the oldest go.
const keepForAccount = (account: string, pending: PendingUiEvent[]): void => {
  for (const { event, seq } of pending) {
    storage.setItem(keptKey(event.event_id), JSON.stringify({ account, event, seq }));
  }
  const kept = readKept().filter((entry) => entry.account === account);
  kept
    .slice(0, Math.max(0, kept.length - MAX_QUEUE))
    .forEach(({ event }) => storage.removeItem(keptKey(event.event_id)));
};

// Remove events the API has answered from the browser's storage, where they may be kept.
const forgetKept = (eventIds: string[]): void => {
  eventIds.forEach((eventId) => storage.removeItem(keptKey(eventId)));
};

// Keep queued events each under the account of the session it was queued under. One queued
// under a token that names no account is dropped: no account can be named for it, and the API
// stores no event without one.
const keepUnderTheirAccounts = (pending: PendingUiEvent[]): void => {
  const byAccount = new Map<string, PendingUiEvent[]>();
  for (const queued of pending) {
    if (queued.account) byAccount.set(queued.account, [...(byAccount.get(queued.account) ?? []), queued]);
  }
  byAccount.forEach((events, account) => keepForAccount(account, events));
};

// Whether the session of `account` may send the event: only one queued under that account.
const sendableUnder =
  (account: string | null) =>
  (pending: PendingUiEvent): boolean =>
    pending.account === account;

// The account of the session's token now; undefined with no session.
const sessionAccount = (): string | null | undefined => {
  const token = getAuthToken();
  return token ? tokenAccount(token) : undefined;
};

// Set aside what a flush with the session's token (null: none) may not send, and return the
// session's account: an event queued under another account waits under its own, never sent under
// this session, and with no session (another request's 401 ended it) every event waits under its
// account, instead of being dropped.
const setAside = (token: string | null): string | null => {
  const account = token ? tokenAccount(token) : null;
  const mayGo = (pending: PendingUiEvent): boolean => token !== null && sendableUnder(account)(pending);
  const aside = queue.filter((pending) => !mayGo(pending));
  if (aside.length) {
    keepUnderTheirAccounts(aside);
    queue = queue.filter(mayGo);
  }
  return account;
};

// Put the events kept for this session's account at the front of the queue, as many as it has
// room for: they were made before the events queued since. They stay kept until the API has
// answered them (settle), so a page that ends first loses none; one already queued, or on its
// way in a page-close keepalive, is not queued twice. Another account's stay kept.
const restoreKept = (account: string): void => {
  const room = MAX_QUEUE - queue.length;
  if (room <= 0) return;
  const queued = new Set(queue.map(({ event }) => event.event_id));
  const restored = readKept()
    .filter(
      (kept) =>
        kept.account === account && !queued.has(kept.event.event_id) && !onTheirWay.has(kept.event.event_id),
    )
    .slice(0, room)
    .map(({ event, seq }) => ({ event, account, seq }));
  if (restored.length) queue = [...restored, ...queue];
};

// Take a batch the API has answered out of the queue, where the flush at page hide has not
// already taken it, and out of the browser's storage, where it may be kept.
const settle = (batch: PendingUiEvent[]): void => {
  queue = queue.filter((pending) => !batch.includes(pending));
  forgetKept(batch.map(({ event }) => event.event_id));
};

// Renew a Microsoft session's token before it expires, as the API client does before a
// request: the flush at page hide or unload sends the token the session holds, and cannot wait
// for a renewal. A password session's token is not renewed.
const keepTokenFresh = async (): Promise<void> => {
  const token = getAuthToken();
  if (!token || getSessionSignIn() !== 'microsoft') return;
  const expiresAt = tokenExpiresAt(token);
  const now = Date.now();
  if (expiresAt === null || expiresAt - now > RENEW_AHEAD_MS || now < renewalDueAt) return;
  renewalDueAt = now + RENEW_RETRY_MS;
  try {
    if ((await sessionAccessToken()) !== token) renewalDueAt = 0;
  } catch {
    // Telemetry never disrupts the app: the next renewal is asked a minute later.
  }
};

// Best-effort flush via axios. Events of another account, or of a session that ended, wait
// under theirs (setAside); logout flushes the queue first (`flushUiEventsNow`). A batch stays in
// the queue until the API acknowledges it, so a page closed while it is in flight sends it again
// with the rest (flushKeepalive), and the API stores an event that did arrive once, by its
// event_id. A batch that fails transiently stays at the front of the queue for the next flush
// instead of being lost (#521); the queue stays bounded by MAX_QUEUE. The events kept for the
// session's account go out first, and leave the browser's storage once the API has answered
// them; a batch the API refuses for the session's token (401) is kept for the account, with the
// rest of the queue, rather than dropped.
const flush = async (): Promise<void> => {
  if (flushInFlight) return;
  const token = getAuthToken();
  const account = setAside(token);
  if (!token) return;
  if (account) restoreKept(account);
  if (!queue.length) return;
  flushInFlight = true;
  try {
    // The API client sends a batch with the token the session holds then: a session that ended
    // or changed meanwhile leaves the rest to the next flush, which sets aside what it may not send.
    while (sessionAccount() === account) {
      const batch = queue.filter(sendableUnder(account)).slice(0, MAX_BATCH);
      if (!batch.length) break;
      try {
        await api.post('/ui-events', { events: batch.map((pending) => pending.event) });
        settle(batch);
      } catch (error) {
        if (account && isRefusedSession(error)) {
          keepUnderTheirAccounts(queue);
          queue = [];
        } else if (!isTransientFailure(error)) {
          settle(batch);
        }
        // Retry on the next interval rather than looping; telemetry must never
        // disrupt the app.
        break;
      }
    }
  } finally {
    flushInFlight = false;
  }
};

// One page-close keepalive. While it is on its way no flush sends its events again; the API
// taking it (a 2xx) removes them from the browser's storage. Any other answer, or none (the page
// was closed before it came), leaves them kept for the account's next flush the API takes.
const sendKeepalive = (token: string, events: QueuedUiEvent[]): void => {
  const eventIds = events.map(({ event_id }) => event_id);
  eventIds.forEach((eventId) => onTheirWay.add(eventId));
  const answered = (taken: boolean): void => {
    eventIds.forEach((eventId) => onTheirWay.delete(eventId));
    if (taken) forgetKept(eventIds);
  };
  try {
    void fetch(`${apiBaseUrl}/ui-events`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${token}`,
      },
      body: JSON.stringify({ events }),
      keepalive: true,
    }).then(
      (response) => answered(response.ok),
      () => answered(false),
    );
  } catch {
    // Telemetry is best-effort and never disrupts the app: the events stay kept.
    answered(false);
  }
};

// Synchronous flush for page-hide / unload, when an async request would be
// cancelled. `fetch(..., { keepalive: true })` survives unload and lets us
// attach the bearer token (sendBeacon cannot set the Authorization header).
// The whole queue goes, a batch the API client has in flight included: the page closing cancels
// that request, whether or not it arrived. It is kept for the account before it is sent, and
// stays kept until the API takes it: a keepalive that fails, is refused, or whose answer the
// closing page never reads goes out again with the account's next flush the API takes, and the
// API stores an event that did arrive once, by its event_id (SEC-36; before, nothing read the
// answer). The token is the session's, which the flush timer keeps renewed (keepTokenFresh); one
// that has expired anyway (the device slept, or MSAL needs the user) would be refused, so the
// events are only kept.
const flushKeepalive = (): void => {
  const token = getAuthToken();
  const account = setAside(token);
  if (!token || !queue.length) return;
  const pending = queue.splice(0, queue.length);
  if (account) keepForAccount(account, pending);
  if (account && hasExpired(token)) return;
  for (let i = 0; i < pending.length; i += MAX_KEEPALIVE_BATCH) {
    sendKeepalive(token, pending.slice(i, i + MAX_KEEPALIVE_BATCH).map(({ event }) => event));
  }
};

const describeTarget = (
  target: EventTarget | null,
): Pick<UiEventInput, 'category' | 'label' | 'target_id' | 'href'> | null => {
  if (!(target instanceof Element)) return null;
  const el = target.closest<HTMLElement>(INTERACTIVE_SELECTOR);
  if (!el) return null;

  const tag = el.tagName.toLowerCase();
  const role = el.getAttribute('role');
  let category: string;
  if (el.dataset.auditCategory) {
    category = el.dataset.auditCategory;
  } else if (role) {
    category = role;
  } else if (tag === 'a') {
    category = 'link';
  } else if (tag === 'input') {
    category = el.getAttribute('type') === 'submit' ? 'submit' : 'button';
  } else {
    category = tag;
  }

  const label =
    el.dataset.auditLabel ||
    el.getAttribute('aria-label') ||
    el.textContent ||
    el.getAttribute('title') ||
    (el as HTMLInputElement).value ||
    undefined;

  const href = el instanceof HTMLAnchorElement ? el.getAttribute('href') || undefined : undefined;
  const targetId = el.dataset.auditId || el.id || undefined;

  return { category, label, target_id: targetId, href };
};

const handleDocumentClick = (event: MouseEvent): void => {
  const described = describeTarget(event.target);
  if (!described) return;
  logUiEvent({
    event_type: 'click',
    path: window.location.pathname + window.location.search,
    ...described,
  });
};

/**
 * Send everything queued now, with the current session's token, surviving a
 * navigation. Called on logout before the session is cleared, so the logout click
 * and the events before it are recorded under the user who made them (#521). They
 * stay kept for the account until the API takes them; when that token has expired,
 * they are only kept, for the account's next sign-in.
 */
export const flushUiEventsNow = (): void => {
  flushKeepalive();
};

/** Flush the queue through the API client now (what the interval timer does). */
export const flushUiEvents = (): Promise<void> => flush();

/**
 * Start global UI telemetry. Idempotent — safe to call from a React effect that
 * may run more than once. Returns a cleanup function.
 */
export const startUiTelemetry = (): (() => void) => {
  if (started || typeof document === 'undefined') {
    return () => {};
  }
  started = true;

  document.addEventListener('click', handleDocumentClick, { capture: true });
  document.addEventListener('submit', handleDocumentSubmit, { capture: true });
  flushTimer = setInterval(() => {
    void keepTokenFresh();
    void flush();
  }, FLUSH_INTERVAL_MS);

  const handleVisibility = (): void => {
    if (document.visibilityState === 'hidden') {
      flushKeepalive();
    }
    // Renewed now when it is due: a hidden page may be closed with no flush in between, and a
    // page shown again after the device slept holds an old token.
    void keepTokenFresh();
  };
  document.addEventListener('visibilitychange', handleVisibility);
  window.addEventListener('pagehide', flushKeepalive);

  return () => {
    document.removeEventListener('click', handleDocumentClick, { capture: true });
    document.removeEventListener('submit', handleDocumentSubmit, { capture: true });
    document.removeEventListener('visibilitychange', handleVisibility);
    window.removeEventListener('pagehide', flushKeepalive);
    if (flushTimer) {
      clearInterval(flushTimer);
      flushTimer = null;
    }
    started = false;
  };
};

function handleDocumentSubmit(event: Event): void {
  const form = event.target;
  if (!(form instanceof HTMLFormElement)) return;
  logUiEvent({
    event_type: 'submit',
    category: 'form',
    label: form.getAttribute('aria-label') || form.getAttribute('name') || undefined,
    target_id: form.dataset.auditId || form.id || undefined,
    path: window.location.pathname + window.location.search,
  });
}
