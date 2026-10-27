// @vitest-environment node
// The /api proxy in server.mjs — #521: an upstream reset mid-stream could crash the
// server, there was no upstream timeout, the 502 named the internal backend URL, and
// client-supplied X-Forwarded-For went to the backend unchecked. Its own 502 and 504 were
// sent without the server's security headers, which only its later middleware set.

import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { afterAll, beforeAll, describe, expect, it, vi } from 'vitest';

import { clientAddress, forwardedHeaders } from '../../proxyRequest.mjs';
import { CSP, PERMISSIONS_POLICY } from '../../securityHeaders.mjs';

// The API's own policy, as backend/app/middleware/security_headers.py sends it.
const BACKEND_CSP = "default-src 'none'; frame-ancestors 'none'";
const HSTS = 'max-age=63072000; includeSubDomains';

/** The server's security headers on an answer (securityHeaders.mjs), HSTS aside. */
const expectSecurityHeaders = (res: Response) => {
  expect(res.headers.get('content-security-policy')).toBe(CSP);
  expect(res.headers.get('x-content-type-options')).toBe('nosniff');
  expect(res.headers.get('x-frame-options')).toBe('DENY');
  expect(res.headers.get('referrer-policy')).toBe('no-referrer');
  expect(res.headers.get('cross-origin-opener-policy')).toBe('same-origin');
  expect(res.headers.get('permissions-policy')).toBe(PERMISSIONS_POLICY);
};

let backend: http.Server;
let frontend: http.Server;
let base = '';
let backendUrl = '';
const seen: Array<Record<string, string | string[] | undefined>> = [];

const listen = (server: http.Server) =>
  new Promise<string>((resolve) =>
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`)),
  );

beforeAll(async () => {
  backend = http.createServer((req, res) => {
    seen.push(req.headers);
    if (req.url?.startsWith('/api/reset')) {
      res.writeHead(200, { 'content-type': 'application/json' });
      res.write('{"partial": ');
      setTimeout(() => res.socket?.destroy(), 20);
      return;
    }
    if (req.url?.startsWith('/api/hang')) return; // never answers
    if (req.url?.startsWith('/api/own-headers')) {
      // The headers the API sets on its own answers.
      res.writeHead(200, {
        'content-type': 'application/json',
        'content-security-policy': BACKEND_CSP,
        'x-frame-options': 'DENY',
      });
      res.end(JSON.stringify({ ok: true }));
      return;
    }
    res.writeHead(200, { 'content-type': 'application/json' });
    res.end(JSON.stringify({ ok: true }));
  });
  backendUrl = await listen(backend);
  process.env.BACKEND_URL = backendUrl;
  process.env.API_PROXY_TIMEOUT_MS = '300';
  const { app } = await import('../../server.mjs');
  frontend = http.createServer(app);
  base = await listen(frontend);
});

afterAll(async () => {
  await new Promise((resolve) => frontend.close(resolve));
  backend.closeAllConnections?.();
  await new Promise((resolve) => backend.close(resolve));
  delete process.env.BACKEND_URL;
  delete process.env.API_PROXY_TIMEOUT_MS;
});

describe('API proxy', () => {
  it('survives an upstream reset mid-stream and keeps serving', async () => {
    await fetch(`${base}/api/reset`).then((r) => r.text()).catch(() => undefined);
    const after = await fetch(`${base}/api/ok`);
    expect(after.status).toBe(200);
    expect(await after.json()).toEqual({ ok: true });
  });

  it('answers 504 when the backend does not respond in time', async () => {
    const res = await fetch(`${base}/api/hang`);
    expect(res.status).toBe(504);
  });

  it("gives its own 504 the server's security headers, HSTS only as ENABLE_HSTS says", async () => {
    vi.stubEnv('ENABLE_HSTS', '');
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    try {
      const res = await fetch(`${base}/api/hang`);
      expect(res.status).toBe(504);
      // Before: none of them; the security-headers middleware runs after the proxy.
      expectSecurityHeaders(res);
      expect(res.headers.get('strict-transport-security')).toBeNull();

      vi.stubEnv('ENABLE_HSTS', '1');
      const behindTls = await fetch(`${base}/api/hang`);
      expect(behindTls.status).toBe(504);
      expect(behindTls.headers.get('strict-transport-security')).toBe(HSTS);
    } finally {
      vi.unstubAllEnvs();
      logged.mockRestore();
    }
  });

  it("relays the backend's answer with the backend's own headers, not the app page's", async () => {
    const res = await fetch(`${base}/api/own-headers`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-security-policy')).toBe(BACKEND_CSP);
    expect(res.headers.get('x-frame-options')).toBe('DENY');
    expect(res.headers.get('permissions-policy')).toBeNull();
  });

  it('sends the backend one clean client address, not what the client claimed', async () => {
    seen.length = 0;
    await fetch(`${base}/api/ok`, { headers: { 'x-forwarded-for': '6.6.6.6' } });
    expect(seen.at(-1)?.['x-forwarded-for']).toBe('127.0.0.1');
    expect(seen.at(-1)?.['x-forwarded-proto']).toBe('http');
  });

  it('does not name the internal backend address when it is unreachable', async () => {
    await new Promise((resolve) => backend.close(resolve));
    backend.closeAllConnections?.();
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const res = await fetch(`${base}/api/ok%25s`);
    expect(res.status).toBe(502);
    // The server's own answer: its security headers (before: none).
    expectSecurityHeaders(res);
    const body = await res.text();
    expect(body).not.toContain(backendUrl);
    expect(body).toContain('Unable to reach the backend API');
    // The URL is an argument, never the format string: a "%s" in it stays text (#702).
    expect(logged).toHaveBeenLastCalledWith('API proxy failed for %s %s', 'GET', '/api/ok%25s', expect.anything());
    logged.mockRestore();
    backend = http.createServer();
  });
});

describe('forwarded headers', () => {
  const req = (xff?: string, peer = '::ffff:10.0.0.5') => ({
    headers: { ...(xff ? { 'x-forwarded-for': xff } : {}), host: 'coga.example', 'x-forwarded-proto': 'https' },
    socket: { remoteAddress: peer },
  });

  it('uses the socket peer when this server is the edge', () => {
    expect(clientAddress(req('6.6.6.6'), 0)).toBe('10.0.0.5');
    expect(forwardedHeaders(req('6.6.6.6'), 0)).toEqual({
      'x-forwarded-for': '10.0.0.5',
      'x-forwarded-proto': 'http',
      'x-forwarded-host': 'coga.example',
    });
  });

  it('takes the address a trusted proxy appended, ignoring what the client prepended', () => {
    expect(clientAddress(req('6.6.6.6, 203.0.113.9'), 1)).toBe('203.0.113.9');
    expect(forwardedHeaders(req('203.0.113.9'), 1)['x-forwarded-proto']).toBe('https');
    // Fewer entries than trusted hops: fall back to the peer.
    expect(clientAddress(req(undefined), 1)).toBe('10.0.0.5');
  });
});
