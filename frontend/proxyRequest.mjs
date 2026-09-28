// Helpers for the /api proxy in server.mjs (#521), kept pure so they can be tested.

/** Time the backend has to start responding before the proxy answers 504. */
export const DEFAULT_PROXY_TIMEOUT_MS = 10 * 60 * 1000;

const positiveInt = (value, fallback) => {
  const parsed = Number.parseInt(String(value ?? ''), 10);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : fallback;
};

export const proxyTimeoutMs = (env = process.env) =>
  positiveInt(env.API_PROXY_TIMEOUT_MS, DEFAULT_PROXY_TIMEOUT_MS) || DEFAULT_PROXY_TIMEOUT_MS;

/**
 * How many reverse proxies in front of this server are trusted to append the address
 * they saw to X-Forwarded-For. 0 (the default) means this server is the edge, so the
 * socket peer is the client and anything the client sent in X-Forwarded-For is ignored.
 */
export const trustedProxyHops = (env = process.env) => positiveInt(env.TRUSTED_PROXY_HOPS, 0);

const firstValue = (value) => String(Array.isArray(value) ? value[0] : (value ?? '')).split(',')[0].trim();

const stripMappedV4 = (address) => (address.startsWith('::ffff:') ? address.slice(7) : address);

/** The client address, as far as this server can establish it. */
export function clientAddress(req, hops = trustedProxyHops()) {
  const peer = stripMappedV4(req.socket?.remoteAddress ?? '');
  if (hops <= 0) return peer;
  const chain = String(req.headers['x-forwarded-for'] ?? '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
  // Each trusted proxy appended the address it saw; the client is `hops` from the right.
  return chain.length >= hops ? chain[chain.length - hops] : peer;
}

/**
 * The X-Forwarded-* headers sent to the backend: one clean client address, and the
 * original protocol and host. Client-supplied values are only kept when they came
 * through a trusted proxy, so a browser cannot choose the address the backend
 * throttles and audits.
 */
export function forwardedHeaders(req, hops = trustedProxyHops()) {
  const viaProxy = hops > 0;
  return {
    'x-forwarded-for': clientAddress(req, hops),
    'x-forwarded-proto':
      (viaProxy && firstValue(req.headers['x-forwarded-proto'])) ||
      (req.socket?.encrypted ? 'https' : 'http'),
    'x-forwarded-host':
      (viaProxy && firstValue(req.headers['x-forwarded-host'])) || String(req.headers.host ?? ''),
  };
}

/** Request headers the proxy sets itself rather than passing through. */
export const PROXY_OWNED_HEADERS = new Set([
  'x-forwarded-for',
  'x-forwarded-proto',
  'x-forwarded-host',
  'forwarded',
]);
