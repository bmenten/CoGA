import { describe, expect, it } from 'vitest';

// The proxy helper lives next to server.mjs (outside src) because it is loaded
// by the production Node server, not the Vite bundle.
import { rewriteProxyLocation } from '../../../proxyLocation.mjs';

const BACKEND = 'http://backend:8000';

describe('rewriteProxyLocation', () => {
  it('rewrites an internal-host redirect to a same-origin path', () => {
    // The bug: the backend returns this on `/api/families` (no trailing slash).
    expect(rewriteProxyLocation('http://backend:8000/api/families/', BACKEND)).toBe(
      '/api/families/',
    );
  });

  it('tolerates a trailing slash on the backend URL', () => {
    expect(rewriteProxyLocation('http://backend:8000/api/x', 'http://backend:8000/')).toBe(
      '/api/x',
    );
  });

  it('leaves same-origin / relative Locations untouched', () => {
    expect(rewriteProxyLocation('/api/families/', BACKEND)).toBe('/api/families/');
  });

  it('leaves external redirects (e.g. OAuth) untouched', () => {
    const external = 'https://login.example.com/authorize?x=1';
    expect(rewriteProxyLocation(external, BACKEND)).toBe(external);
  });

  it('keeps a backend path on this site, never naming another host (UI-3)', () => {
    // `//host` and `/\host` are read by a browser as another host (a protocol-relative URL).
    expect(rewriteProxyLocation('http://backend:8000//evil.example/x', BACKEND)).toBe('/evil.example/x');
    expect(rewriteProxyLocation('http://backend:8000/\\evil.example/x', BACKEND)).toBe('/evil.example/x');
    expect(rewriteProxyLocation('http://backend:8000/api//x', BACKEND)).toBe('/api//x');
    // The backend's origin itself, with a query or a fragment.
    expect(rewriteProxyLocation('http://backend:8000', BACKEND)).toBe('/');
    expect(rewriteProxyLocation('http://backend:8000?next=1', BACKEND)).toBe('/?next=1');
    expect(rewriteProxyLocation('http://backend:8000#top', BACKEND)).toBe('/#top');
    // A host whose name merely starts with the backend's is another host's.
    const lookalike = 'http://backend:8000.example/x';
    expect(rewriteProxyLocation(lookalike, BACKEND)).toBe(lookalike);
  });

  it('passes through missing / non-string values', () => {
    expect(rewriteProxyLocation(undefined, BACKEND)).toBeUndefined();
    expect(rewriteProxyLocation('', BACKEND)).toBe('');
  });
});
