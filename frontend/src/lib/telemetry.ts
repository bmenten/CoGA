// Client-side UI interaction telemetry.
//
// Captures interactive-element clicks (buttons, links, tabs, menu items, form
// submits) and in-app navigations, batches them, and POSTs them to
// `/ui-events` for storage. This complements the backend request audit log,
// which already records every HTTP call: these are the interactions that never
// reach the backend. Identifier masking (paths -> :id, query strings -> keys)
// happens server-side; the client only sends element labels and structure.
import api, { apiBaseUrl } from './api';
import { getAuthToken } from './auth';

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
// error. A 4xx will not succeed on retry, so that batch is dropped.
const isTransientFailure = (error: unknown): boolean => {
  const status = (error as { response?: { status?: number } })?.response?.status;
  return status === undefined || status === 429 || status >= 500;
};

// Best-effort flush via axios. Without a token nothing can be attributed to a user,
// so the queue is dropped; logout flushes it first (`flushUiEventsNow`). A batch that
// fails transiently goes back to the front of the queue for the next flush instead of
// being lost (#521); the queue stays bounded by MAX_QUEUE.
const flush = async (): Promise<void> => {
  if (flushInFlight || !queue.length) return;
  if (!getAuthToken()) {
    queue = [];
    return;
  }
  flushInFlight = true;
  try {
    while (queue.length) {
      const events = takeBatch();
      try {
        await api.post('/ui-events', { events });
      } catch (error) {
        if (isTransientFailure(error)) {
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
const flushKeepalive = (): void => {
  const token = getAuthToken();
  if (!queue.length || !token) {
    queue = [];
    return;
  }
  const pending = queue.splice(0, queue.length);
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
 * and the events before it are recorded under the user who made them (#521).
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
    void flush();
  }, FLUSH_INTERVAL_MS);

  const handleVisibility = (): void => {
    if (document.visibilityState === 'hidden') {
      flushKeepalive();
    }
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
