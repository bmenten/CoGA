// Every family page warns of what keeps the family's data from being read as complete and
// validated: an import of the family that partly failed or has not finished (*Import
// incomplete*), and a reference assembly outside the validated scope (*Not validated for
// clinical use*). A page that left them out showed a partly imported family as complete, and
// the NIPT report printed it so, with no sign-out to stop it.
//
// This walks the app's routes (src/index.tsx): every route whose path holds `:familyId` is
// rendered with the page its element lazy-loads, for a family on an off-scope assembly whose
// import is incomplete, and must show both warnings. A new family route fails here until its
// page shows them and it has its entry in PAGE_DATA. (The report route is rendered for a family
// never signed out, as the live report; a signed version shows the import state frozen in its
// record instead, and checks it against the family's current one.)
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { QueryClientProvider } from '@tanstack/react-query';
import { render } from '@testing-library/react';
import type { ComponentType } from 'react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../test/familyPageBanners';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
  post: vi.fn(),
  put: vi.fn(),
  patch: vi.fn(),
  delete: vi.fn(),
  defaults: { baseURL: 'http://test-api' },
}));

vi.mock('../lib/api', () => ({ default: apiMock }));

// The IGV viewer loads igv.js from the network; what it draws is not what this checks.
vi.mock('../components/IgvViewer', () => ({ default: () => <div data-testid="igv-viewer" /> }));

// Vitest runs from frontend/.
const APP_SOURCE = readFileSync(path.resolve(process.cwd(), 'src/index.tsx'), 'utf8');

/** `const Page = lazy(() => import('./pages/…/Page'))`: the module each page name loads. */
const LAZY_MODULES = new Map(
  [...APP_SOURCE.matchAll(/const (\w+) = lazy\(\s*\(\) => import\('([^']+)'\)/g)].map(
    ([, name, module]) => [name, module] as const,
  ),
);

/** `<Route path="…:familyId…" element={routeElement(<Page flag />)} />`, each with its page. */
const FAMILY_ROUTES = [
  ...APP_SOURCE.matchAll(
    /<Route\s+path="([^"]*:familyId[^"]*)"\s+element=\{routeElement\(<(\w+)((?:\s+\w+)*)\s*\/>\)\}/g,
  ),
].map(([, routePath, page, flags]) => ({
  routePath,
  page,
  // Boolean props only (`editable`): anything else would not match, and fails below.
  props: Object.fromEntries(flags.trim().split(/\s+/).filter(Boolean).map((flag) => [flag, true])),
}));

/** The pages' modules, by their path from src/: what `lazy(() => import(…))` names. */
const PAGE_MODULES = import.meta.glob<{ default: ComponentType<Record<string, unknown>> }>([
  '../pages/**/*.tsx',
  '!../pages/**/__tests__/**',
]);

const pageModule = (page: string) => {
  const module = LAZY_MODULES.get(page);
  if (!module) throw new Error(`index.tsx loads no page named ${page}`);
  const load = PAGE_MODULES[`../${module.replace(/^\.\//, '')}.tsx`];
  if (!load) throw new Error(`no module ${module}.tsx for ${page}`);
  return load();
};

/** A value for each route parameter. */
const PARAMS: Record<string, string> = { familyId: 'F1', chrom: '1' };

const urlOf = (routePath: string) =>
  routePath.replace(/:(\w+)/g, (_match, name: string) => {
    if (!(name in PARAMS)) throw new Error(`no value for :${name} in PARAMS`);
    return PARAMS[name];
  });

interface PageData {
  /** Fields of the family record the page needs to get past its own checks. */
  family?: Record<string, unknown>;
  /** What the page asks for besides the family and its project, by path (without query). */
  api?: Record<string, unknown>;
}

const NIPT_FAMILY = { metadata: { analysis_type: 'monogenic_nipt', ...INCOMPLETE_IMPORT_METADATA } };
const NO_VARIANTS = { total: 0, variants: [] };

/**
 * What each family route's page needs to show the family, beyond the family record and its
 * project: the page's own data, where the page shows a failure of it in place of the whole
 * page. Any other request fails, as on a server error, so a page that drops its header
 * (warnings and all) over a secondary request is caught.
 */
const PAGE_DATA: Record<string, PageData> = {
  '/families/:familyId': {},
  '/families/:familyId/genome': {},
  '/families/:familyId/chromosome/:chrom': {},
  '/families/:familyId/circos': {
    api: {
      '/chromosomes/GRCh37/details': [...Array.from({ length: 22 }, (_, index) => String(index + 1)), 'X', 'Y'].map(
        (chr) => ({ _id: `chr-${chr}`, assembly_id: 'assembly-37', chr, size: 1_000_000, bands: [] }),
      ),
    },
  },
  '/families/:familyId/structural-variants': {},
  '/families/:familyId/small-variants': { api: { '/families/F1/small-variants': NO_VARIANTS } },
  '/families/:familyId/roi-markers': {
    family: { roi: { chr: '1', start: 1_000_000, end: 1_001_000, label: 'GENEX', source: 'gene', query: 'GENEX' } },
  },
  '/families/:familyId/report': {
    api: { '/families/F1/small-variants': NO_VARIANTS, '/families/F1/structural-variants': NO_VARIANTS },
  },
  '/families/:familyId/qc': {
    api: {
      '/families/F1/qc/sample-integrity': {
        family_id: 'F1',
        overall_status: 'pass',
        application: 'wgs',
        application_label: 'Short-read WGS family',
        application_summary: 'Full pedigree QC on the SNV call set.',
        genotype_source: 'deepvariant',
        paternity_check: null,
        autosomal_sites: 90000,
        notes: [],
        sex_checks: [],
        relatedness_checks: [],
        mendelian_checks: [],
      },
    },
  },
  '/families/:familyId/variant-summary': {
    api: {
      '/families/F1/structural-variant-lengths': [],
      '/families/F1/shared-structural-variant-counts': {},
    },
  },
  '/families/:familyId/repeat-expansions': { api: { '/families/F1/repeat-expansions': { samples: [], loci: [] } } },
  '/families/:familyId/paraphase': { api: { '/families/F1/paraphase': { samples: [], genes: [] } } },
  '/families/:familyId/mitochondrial-dna': {
    api: {
      '/families/F1/mitochondrial-dna': {
        samples: [],
        variants: [],
        structural_variants: [],
        qc_notes: [],
        heteroplasmy_threshold: 0.02,
        homoplasmy_threshold: 0.95,
      },
    },
  },
  '/families/:familyId/nipt': { family: NIPT_FAMILY },
  '/families/:familyId/nipt/report': { family: NIPT_FAMILY, api: { '/families/F1/nipt/variants': NO_VARIANTS } },
  '/families/:familyId/nipt/coverage': { family: NIPT_FAMILY },
  '/families/:familyId/igv': {},
  '/admin/data/families/:familyId/structure': {},
};

/** Family F1, on project p1 (GRCh37, outside the validated scope), its import incomplete. */
const FAMILY = {
  _id: 'family-1',
  family_id: 'F1',
  created_at: '2026-09-01T00:00:00+00:00',
  members: [],
  relationships: [],
  structure_version: null,
  pedigree: null,
  roi: null,
  projects: ['p1'],
  status: null,
  assigned_to: null,
  reviewed_by: null,
  metadata: INCOMPLETE_IMPORT_METADATA,
};

const serve = ({ family = {}, api = {} }: PageData) => {
  const table: Record<string, unknown> = {
    '/families/F1': { ...FAMILY, ...family },
    '/projects': [offScopeProject('p1')],
    ...api,
  };
  apiMock.get.mockImplementation((url: string) => {
    const key = url.split('?')[0];
    return key in table
      ? Promise.resolve({ data: table[key] })
      : Promise.reject(
          Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }),
        );
  });
};

beforeEach(() => {
  apiMock.get.mockReset();
  for (const method of [apiMock.post, apiMock.put, apiMock.patch, apiMock.delete]) {
    method.mockResolvedValue({ data: {} });
  }
});

describe('the family routes', () => {
  it('are all found in index.tsx, each with its page', () => {
    const declared = [...APP_SOURCE.matchAll(/\bpath="([^"]*:familyId[^"]*)"/g)].map(([, routePath]) => routePath);
    // A family route whose element this cannot read would be skipped: it fails here instead.
    expect(FAMILY_ROUTES.map((route) => route.routePath)).toEqual(declared);
    // Each one is in PAGE_DATA, and PAGE_DATA names no route the app no longer has.
    expect(Object.keys(PAGE_DATA).sort()).toEqual([...declared].sort());
  });

  it.each(FAMILY_ROUTES)(
    '$routePath ($page) warns of the incomplete import and the off-scope assembly',
    async ({ routePath, page, props }) => {
      serve(PAGE_DATA[routePath] ?? {});
      const Page = (await pageModule(page)).default;

      render(
        <QueryClientProvider client={createTestQueryClient()}>
          <MemoryRouter initialEntries={[urlOf(routePath)]}>
            <Routes>
              <Route path={routePath} element={<Page {...props} />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );

      await findImportIncompleteBanner();
      await findAssemblyScopeBanner();
    },
  );
});
