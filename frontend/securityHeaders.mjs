// Security response headers for the SPA shell (served by server.mjs). Applied to the
// HTML/static responses only — the /api proxy forwards the backend's own headers.
//
// The CSP is enforcing, widened only where the app genuinely needs it:
//   - 'wasm-unsafe-eval'      IGV compiles WebAssembly (e.g. CRAM decoding)
//   - img-src/worker-src blob: IGV/canvas render via blob URLs and web workers
//   - connect-src https:       presigned-S3 host (config-driven) + remote IGV tracks
//   - Google Fonts            style/font from fonts.googleapis.com / fonts.gstatic.com
//   - frame-ancestors 'none'   blocks clickjacking (no view is meant to be embedded)
// If an unforeseen origin is blocked in staging, switch the header name to
// `Content-Security-Policy-Report-Only` to observe before enforcing.
//
// connect-src defaults to any HTTPS host (#521): IGV loads its hosted genomes from a
// changing set of hosts (igv.org, several S3 buckets, UCSC) and a presigned CRAM URL
// points at whichever object store the backend uses, so a fixed list would break the
// genome browser. A deployment that knows its hosts should narrow it with
// CSP_CONNECT_SRC, e.g. "'self' https://storage.googleapis.com https://*.igv.org".
// 'unsafe-inline' stays in style-src because IGV injects inline styles.
export const DEFAULT_CONNECT_SRC = "'self' https:";

export const connectSrc = (env = process.env) => {
  const configured = (env.CSP_CONNECT_SRC ?? '').trim();
  // A source list only: a ';' or a line break would add directives to the policy.
  if (!configured || /[;\r\n]/.test(configured)) return DEFAULT_CONNECT_SRC;
  return configured;
};

export const buildCsp = (env = process.env) => [
  "default-src 'self'",
  "script-src 'self' 'wasm-unsafe-eval'",
  "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
  "font-src 'self' https://fonts.gstatic.com data:",
  "img-src 'self' data: blob:",
  `connect-src ${connectSrc(env)}`,
  "worker-src 'self' blob:",
  "frame-ancestors 'none'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
].join('; ');

export const CSP = buildCsp();

// No view uses these browser features; deny them outright (#521).
export const PERMISSIONS_POLICY = [
  'camera=()',
  'microphone=()',
  'geolocation=()',
  'payment=()',
  'usb=()',
  'serial=()',
  'bluetooth=()',
  'hid=()',
  'midi=()',
  'accelerometer=()',
  'gyroscope=()',
  'magnetometer=()',
  'display-capture=()',
].join(', ');

/**
 * Express middleware that sets the SPA security headers. HSTS is read at call time
 * (opt-in via ENABLE_HSTS) so it is only emitted behind a TLS-terminating proxy.
 */
export function securityHeaders(_req, res, next) {
  res.setHeader('Content-Security-Policy', CSP);
  res.setHeader('X-Content-Type-Options', 'nosniff');
  res.setHeader('X-Frame-Options', 'DENY');
  res.setHeader('Referrer-Policy', 'no-referrer');
  res.setHeader('Cross-Origin-Opener-Policy', 'same-origin');
  res.setHeader('Permissions-Policy', PERMISSIONS_POLICY);
  if (process.env.ENABLE_HSTS === 'true') {
    res.setHeader('Strict-Transport-Security', 'max-age=63072000; includeSubDomains');
  }
  next();
}
