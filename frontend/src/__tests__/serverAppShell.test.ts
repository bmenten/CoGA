// @vitest-environment node
// The build and the app shell as server.mjs serves them. A tab opened before a
// release asked for a page file the release had replaced, and got the app shell, an HTML page
// with 200, which the browser cannot run: "Failed to fetch dynamically imported module".

import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import { tmpdir } from 'node:os';
import path from 'node:path';

import { afterAll, beforeAll, describe, expect, it } from 'vitest';

import { CSP } from '../../securityHeaders.mjs';
import { ASSET_CACHE_CONTROL, SHELL_CACHE_CONTROL, createApp } from '../../server.mjs';

const SHELL = '<!doctype html><html><body><div id="root"></div></body></html>';

let dist = '';
let unbuilt = '';
const servers: http.Server[] = [];

const serve = (distPath: string) =>
  new Promise<string>((resolve) => {
    const server = http.createServer(createApp(distPath));
    servers.push(server);
    server.listen(0, '127.0.0.1', () => resolve(`http://127.0.0.1:${(server.address() as AddressInfo).port}`));
  });

let base = '';

beforeAll(async () => {
  dist = mkdtempSync(path.join(tmpdir(), 'coga-dist-'));
  mkdirSync(path.join(dist, 'assets'));
  writeFileSync(path.join(dist, 'index.html'), SHELL);
  writeFileSync(path.join(dist, 'assets', 'SomePage-NEWHASH.js'), 'export default 1;\n');
  writeFileSync(path.join(dist, 'favicon.svg'), '<svg xmlns="http://www.w3.org/2000/svg"/>');
  unbuilt = mkdtempSync(path.join(tmpdir(), 'coga-unbuilt-'));
  base = await serve(dist);
});

afterAll(async () => {
  await Promise.all(servers.map((server) => new Promise((resolve) => server.close(resolve))));
  rmSync(dist, { recursive: true, force: true });
  rmSync(unbuilt, { recursive: true, force: true });
});

describe('app shell', () => {
  it('serves a build file to keep, by its content-hashed name', async () => {
    const res = await fetch(`${base}/assets/SomePage-NEWHASH.js`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toMatch(/javascript/);
    expect(res.headers.get('cache-control')).toBe(ASSET_CACHE_CONTROL);
  });

  it('answers 404, not the app shell, for a build file a release replaced', async () => {
    const res = await fetch(`${base}/assets/SomePage-OLDHASH.js`);
    // Before: 200 with the app shell's HTML.
    expect(res.status).toBe(404);
    expect(res.headers.get('content-type')).toMatch(/^text\/plain/);
    expect(res.headers.get('cache-control')).toBe('no-store');
    expect(res.headers.get('content-security-policy')).toBe(CSP);
    expect(await res.text()).not.toContain('<html');
  });

  it('revalidates the app shell on every load, at / as on every route', async () => {
    for (const route of ['/', '/index.html', '/families/FAM1']) {
      const res = await fetch(`${base}${route}`);
      expect(res.status).toBe(200);
      expect(res.headers.get('content-type')).toMatch(/^text\/html/);
      // Before, `/` was the build's index.html as a static file: "public, max-age=0".
      expect(res.headers.get('cache-control')).toBe(SHELL_CACHE_CONTROL);
      expect(await res.text()).toBe(SHELL);
    }
  });

  it('serves the files at the top of the build', async () => {
    const res = await fetch(`${base}/favicon.svg`);
    expect(res.status).toBe(200);
    expect(res.headers.get('content-type')).toMatch(/^image\/svg\+xml/);
  });

  it('answers 503 for the app shell of an app not built', async () => {
    const unbuiltBase = await serve(unbuilt);
    const res = await fetch(`${unbuiltBase}/families/FAM1`);
    expect(res.status).toBe(503);
    expect(await res.text()).toBe('The app is not built.');
  });
});
