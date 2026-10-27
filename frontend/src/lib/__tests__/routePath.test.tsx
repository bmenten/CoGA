// In-app routes encode the identifiers they carry (SEC-15): raw, an ID with `?`, `#` or `/`
// broke in-app navigation.

import { readdirSync, readFileSync } from 'node:fs';
import path from 'node:path';

import { render } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation, useParams } from 'react-router';
import { describe, expect, it } from 'vitest';

import { familyPagePath, raw, routePath } from '../routePath';

const Shown = () => {
  const { familyId, chrom } = useParams();
  const location = useLocation();
  return <output>{JSON.stringify({ familyId, chrom, search: location.search, hash: location.hash })}</output>;
};

// What the chromosome route reads from `path`, or null when no route matches it.
const opened = (path: string): Record<string, string> | null => {
  const { container, unmount } = render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route path="/families/:familyId/chromosome/:chrom" element={<Shown />} />
        <Route path="*" element={null} />
      </Routes>
    </MemoryRouter>,
  );
  const text = container.querySelector('output')?.textContent;
  unmount();
  return text ? (JSON.parse(text) as Record<string, string>) : null;
};

describe('routePath', () => {
  it('gives the route the identifier as written, whatever it holds', () => {
    for (const familyId of ['F?1', 'F#1', 'F%1', 'F/1', 'F 1', '..', 'FAM-001']) {
      expect(opened(routePath`/families/${familyId}/chromosome/${'X'}${raw('?start=1')}`)).toEqual({
        familyId,
        chrom: 'X',
        search: '?start=1',
        hash: '',
      });
    }
  });

  it('is what a raw route got wrong', () => {
    // Raw, `?` and `#` cut the path short and `/` added a segment: no route matched.
    for (const familyId of ['F?1', 'F#1', 'F/1']) {
      expect(opened(`/families/${familyId}/chromosome/X`)).toBeNull();
    }
  });

  it('leaves an ordinary identifier unchanged', () => {
    expect(routePath`/families/${'FAM-001'}/report${raw('?version=2')}`).toBe('/families/FAM-001/report?version=2');
  });
});

// Every in-app route that carries an identifier is built with routePath (or apiPath): a raw
// template literal such as `/families/${familyId}` breaks on an ID with `?`, `#`, `%` or `/`
// (SEC-15). This scans the app's source, tests left out, for such a template.
describe('the app builds routes with identifiers through routePath', () => {
  const sourceRoot = path.resolve(process.cwd(), 'src');
  const sources = (dir: string): string[] =>
    readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
      const full = path.join(dir, entry.name);
      if (entry.isDirectory()) return entry.name === '__tests__' || entry.name === 'test' ? [] : sources(full);
      return /\.tsx?$/.test(entry.name) && !/\.test\.tsx?$/.test(entry.name) ? [full] : [];
    });
  // A route whose first segment takes an identifier: `/families/:familyId`, `/panels/:panelId`,
  // `/cnv-details/:cnvId`, `/report-codes/:code`.
  const RAW_ROUTE = /(?<!routePath|apiPath)`\/(?:families|panels|cnv-details|report-codes)\/\$\{/g;
  // A crumb's path is built from the pathname's own segments, already encoded.
  const ALREADY_ENCODED = new Set(['components/Breadcrumbs.tsx']);

  it('finds no route template that leaves an identifier raw', () => {
    const found = sources(sourceRoot).flatMap((file) => {
      const relative = path.relative(sourceRoot, file).split(path.sep).join('/');
      if (ALREADY_ENCODED.has(relative)) return [];
      const text = readFileSync(file, 'utf8');
      return [...text.matchAll(RAW_ROUTE)].map(
        (match) => `${relative}:${text.slice(0, match.index).split('\n').length}`,
      );
    });
    expect(found).toEqual([]);
  });

  it('would find one', () => {
    const matches = (text: string) => [...text.matchAll(RAW_ROUTE)].length;
    expect(matches('<Link to={`/families/${familyId}`}>')).toBe(1);
    expect(matches('navigate(`/panels/${panelId}`)')).toBe(1);
    expect(matches('<Link to={routePath`/families/${familyId}`}>')).toBe(0);
    expect(matches('api.get(apiPath`/families/${familyId}`)')).toBe(0);
  });
});

describe('familyPagePath', () => {
  it('keeps a path to one of the family pages, and nothing else', () => {
    expect(familyPagePath('/families/F1/small-variants?gene=X')).toBe('/families/F1/small-variants?gene=X');
    for (const from of ['https://example.org/x', '//example.org/x', '/admin/users', '/families\\..\\x', '', null, undefined]) {
      expect(familyPagePath(from)).toBeNull();
    }
  });
});
