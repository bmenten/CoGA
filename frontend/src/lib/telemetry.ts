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
  session_id: string;
  occurred_at: string;
};

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
const KEPT_EVENTS_KEY = 'coga.ui-events.kept';

// Interactive elements we treat as meaningful clicks. `data-audit-id` lets a
// component opt a custom element in and give it a stable identifier.
const INTERACTIVE_SELECTOR =
  'button, a[href], [role="button"], [role="tab"], [role="menuitem"], ' +
  'input[type="submit"], input[type="button"], summary, [data-audit-id]';

let queue: QueuedUiEvent[] = [];
let sessionId = '';
let started = false;
let flushTimer: ReturnType<typeof setInterval> | null = null;
let flushInFlight = false;
let renewalDueAt = 0;

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

const nowIso = (): string => new Date().toISOString();

const truncate = (value: string | null | undefined, max: number): string | undefined => {
  if (!value) return undefined;
  const collapsed = value.replace(/\s+/g, ' ').trim();
  return collapsed ? collapsed.slice(0, max) : undefined;
};

export const logUiEvent = (event: UiEventInput): void => {
  if (queue.length >= MAX_QUEUE) {
    queue.shift();
  }
  queue.push({
    ...event,
    label: truncate(event.label, MAX_LABEL_LEN),
    session_id: getSessionId(),
    occurred_at: nowIso(),
  });
};

const takeBatch = (): QueuedUiEvent[] => queue.splice(0, Math.min(MAX_BATCH, queue.length));

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

type KeptUiEvents = { account: string; events: QueuedUiEvent[] };

const isKeptUiEvents = (value: unknown): value is KeptUiEvents =>
  typeof value === 'object' &&
  value !== null &&
  typeof (value as KeptUiEvents).account === 'string' &&
  Array.isArray((value as KeptUiEvents).events);

const readKept = (): KeptUiEvents[] => {
  try {
    const kept: unknown = JSON.parse(storage.getItem(KEPT_EVENTS_KEY) ?? '[]');
    return Array.isArray(kept) ? kept.filter(isKeptUiEvents) : [];
  } catch {
    return [];
  }
};

const writeKept = (entries: KeptUiEvents[]): void => {
  const left = entries.filter((entry) => entry.events.length);
  if (left.length) {
    storage.setItem(KEPT_EVENTS_KEY, JSON.stringify(left));
  } else {
    storage.removeItem(KEPT_EVENTS_KEY);
  }
};

// Keep events the API would refuse for their session's token, under their account, instead of
// sending them to be refused, or dropping them: the UI-event log is part of the audit, and its
// rows are kept for good (#893). Bounded by MAX_QUEUE per account, as the queue is.
const keepForAccount = (account: string, events: QueuedUiEvent[]): void => {
  const entries = readKept();
  const entry = entries.find((kept) => kept.account === account);
  if (entry) {
    entry.events = [...entry.events, ...events].slice(-MAX_QUEUE);
  } else {
    entries.push({ account, events: events.slice(-MAX_QUEUE) });
  }
  writeKept(entries);
};

// Put the events kept for this session's account back at the front of the queue, as many as
// it has room for: they were made before the events queued since. Another account's stay kept.
const restoreKept = (account: string): void => {
  const room = MAX_QUEUE - queue.length;
  const entries = readKept();
  const entry = entries.find((kept) => kept.account === account);
  if (!entry || room <= 0) return;
  queue = [...entry.events.splice(0, room), ...queue];
  writeKept(entries);
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

// Best-effort flush via axios. Without a token nothing can be attributed to a user,
// so the queue is dropped; logout flushes it first (`flushUiEventsNow`). A batch that
// fails transiently goes back to the front of the queue for the next flush instead of
// being lost (#521); the queue stays bounded by MAX_QUEUE. The events kept for the
// session's account go out first; a batch the API refuses for the session's token (401) is
// kept for the account, with the rest of the queue, rather than dropped.
const flush = async (): Promise<void> => {
  if (flushInFlight) return;
  const token = getAuthToken();
  if (!token) {
    queue = [];
    return;
  }
  const account = tokenAccount(token);
  if (account) restoreKept(account);
  if (!queue.length) return;
  flushInFlight = true;
  try {
    while (queue.length) {
      const events = takeBatch();
      try {
        await api.post('/ui-events', { events });
      } catch (error) {
        if (account && isRefusedSession(error)) {
          keepForAccount(account, [...events, ...queue]);
          queue = [];
        } else if (isTransientFailure(error)) {
          queue = [...events, ...queue].slice(-MAX_QUEUE);
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

// Synchronous flush for page-hide / unload, when an async request would be
// cancelled. `fetch(..., { keepalive: true })` survives unload and lets us
// attach the bearer token (sendBeacon cannot set the Authorization header).
// The token is the session's, which the flush timer keeps renewed (keepTokenFresh); one that
// has expired anyway (the device slept, or MSAL needs the user) would be refused, and the
// events lost: they are kept for the account's next flush instead (keepForAccount).
const flushKeepalive = (): void => {
  const token = getAuthToken();
  if (!queue.length || !token) {
    queue = [];
    return;
  }
  const pending = queue.splice(0, queue.length);
  const account = tokenAccount(token);
  if (account && hasExpired(token)) {
    keepForAccount(account, pending);
    return;
  }
  try {
    for (let i = 0; i < pending.length; i += MAX_KEEPALIVE_BATCH) {
      const events = pending.slice(i, i + MAX_KEEPALIVE_BATCH);
      void fetch(`${apiBaseUrl}/ui-events`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${token}`,
        },
        body: JSON.stringify({ events }),
        keepalive: true,
      });
    }
  } catch {
    // ignore — telemetry is best-effort
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
 * and the events before it are recorded under the user who made them (#521). When
 * that token has expired, they are kept for the account's next sign-in instead.
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
