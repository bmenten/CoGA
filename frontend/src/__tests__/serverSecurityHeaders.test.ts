import { describe, expect, it, vi } from 'vitest';

import { CSP, buildCsp, hstsEnabled, securityHeaders } from '../../securityHeaders.mjs';

function mockRes() {
  const headers: Record<string, string> = {};
  return {
    headers,
    setHeader(name: string, value: string) {
      headers[name] = value;
    },
  };
}

describe('SPA securityHeaders middleware', () => {
  it('sets the hardening headers and an IGV/S3-aware enforcing CSP', () => {
    const res = mockRes();
    const next = vi.fn();

    securityHeaders({}, res, next);

    expect(res.headers['X-Content-Type-Options']).toBe('nosniff');
    expect(res.headers['X-Frame-Options']).toBe('DENY');
    expect(res.headers['Referrer-Policy']).toBe('no-referrer');
    expect(res.headers['Cross-Origin-Opener-Policy']).toBe('same-origin');
    expect(res.headers['Content-Security-Policy']).toBe(CSP);
    // Enforcing but widened where the app genuinely needs it.
    expect(CSP).toContain("frame-ancestors 'none'"); // clickjacking
    expect(CSP).toContain("'wasm-unsafe-eval'"); // IGV/CRAM WebAssembly
    expect(CSP).toContain('connect-src'); // presigned S3 / remote tracks
    expect(next).toHaveBeenCalledOnce();
  });

  it('omits HSTS by default (never over plain HTTP)', () => {
    const res = mockRes();
    vi.stubEnv('ENABLE_HSTS', '');
    securityHeaders({}, res, () => {});
    expect(res.headers['Strict-Transport-Security']).toBeUndefined();
    vi.unstubAllEnvs();
  });

  it('emits HSTS only when ENABLE_HSTS=true (TLS-terminated opt-in)', () => {
    const res = mockRes();
    // The value Terraform gives the frontend's Cloud Run service (terraform/cloudrun.tf).
    vi.stubEnv('ENABLE_HSTS', 'true');
    securityHeaders({}, res, () => {});
    // Two years, for this host and its subdomains: the policy the API sends too.
    expect(res.headers['Strict-Transport-Security']).toBe('max-age=63072000; includeSubDomains');
    vi.unstubAllEnvs();
  });

  it('reads ENABLE_HSTS as the backend reads it, so the two never disagree (SEC-23)', () => {
    // Compose hands both containers the one value from .env; the frontend took only `true`.
    for (const on of ['true', 'True', 'TRUE', '1', 'yes', 'y', 'on', 't', ' true ']) {
      expect(hstsEnabled(on)).toBe(true);
    }
    for (const off of ['', 'false', 'False', '0', 'no', 'off', 'maybe', undefined]) {
      expect(hstsEnabled(off)).toBe(false);
    }
    const res = mockRes();
    vi.stubEnv('ENABLE_HSTS', '1');
    securityHeaders({}, res, () => {});
    expect(res.headers['Strict-Transport-Security']).toBe('max-age=63072000; includeSubDomains');
    vi.unstubAllEnvs();
  });
});

describe('SPA hardening added for #521', () => {
  it('denies unused browser features with a Permissions-Policy', () => {
    const res = mockRes();
    securityHeaders({}, res, () => {});
    expect(res.headers['Permissions-Policy']).toContain('camera=()');
    expect(res.headers['Permissions-Policy']).toContain('geolocation=()');
  });

  it('lets a deployment narrow connect-src, but not add directives', () => {
    expect(buildCsp({ CSP_CONNECT_SRC: "'self' https://storage.googleapis.com" })).toContain(
      "connect-src 'self' https://storage.googleapis.com;",
    );
    expect(buildCsp({})).toContain("connect-src 'self' https:;");
    // A ';' would smuggle in another directive, so the value is refused.
    expect(buildCsp({ CSP_CONNECT_SRC: "'self'; script-src *" })).toContain("connect-src 'self' https:;");
    expect(buildCsp({ CSP_CONNECT_SRC: "'self'; script-src *" })).not.toContain('script-src *');
  });
});
