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
//
// The warnings come from the family record, so no page shows the family's data without it
// (SAFE-14). Before the record is in, a page shows a page state alone; when it could not be
// loaded, the page says the family could not be loaded, offers a retry, and shows nothing else.
// Three pages showed their data without the warnings when the record failed, and one waited
// for it for good.
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
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
import { held, settle } from '../test/settle';

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

const serverError = () =>
  Promise.reject(Object.assign(new Error('Request failed with status code 500'), { response: { status: 500 } }));

/**
 * How a request for the family record ends: answered, failed as on a server error, or held
 * until the promise given here resolves. Read at each request, so a retry sees a change.
 */
let familyAnswer: 'answered' | 'failed' | Promise<void> = 'answered';
/** How the request for the project list ends: answered, or held until the promise resolves. */
let projectsAnswer: 'answered' | Promise<void> = 'answered';

const serve = ({ family = {}, api = {} }: PageData) => {
  const record = { ...FAMILY, ...family };
  const table: Record<string, unknown> = {
    '/projects': [offScopeProject('p1')],
    ...api,
  };
  apiMock.get.mockImplementation((url: string) => {
    const key = url.split('?')[0];
    if (key === '/families/F1') {
      if (familyAnswer === 'failed') return serverError();
      if (familyAnswer instanceof Promise) return familyAnswer.then(() => ({ data: record }));
      return Promise.resolve({ data: record });
    }
    if (key === '/projects' && projectsAnswer instanceof Promise) {
      return projectsAnswer.then(() => ({ data: table[key] }));
    }
    return key in table ? Promise.resolve({ data: table[key] }) : serverError();
  });
};

type FamilyRoute = (typeof FAMILY_ROUTES)[number];

const renderRoute = async ({ routePath, page, props }: FamilyRoute) => {
  serve(PAGE_DATA[routePath] ?? {});
  const Page = (await pageModule(page)).default;
  return render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[urlOf(routePath)]}>
        <Routes>
          <Route path={routePath} element={<Page {...props} />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
};

/** The page state (`PageState`) the page shows, and that it shows nothing else. */
const expectPageStateAlone = (container: HTMLElement, state: Element | null) => {
  expect(state).not.toBeNull();
  expect(container.querySelectorAll('.page-state')).toHaveLength(1);
  expect(container.textContent).toBe(state?.textContent);
};

beforeEach(() => {
  apiMock.get.mockReset();
  familyAnswer = 'answered';
  projectsAnswer = 'answered';
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
    async (route) => {
      await renderRoute(route);

      await findImportIncompleteBanner();
      await findAssemblyScopeBanner();
    },
  );

  it.each(FAMILY_ROUTES)(
    '$routePath ($page) shows only that the family could not be loaded, and loads it on retry',
    async (route) => {
      familyAnswer = 'failed';
      const { container } = await renderRoute(route);

      // "Family could not be loaded"; the reports say "The family could not be loaded, …".
      const saysSo = /family could not be loaded/i;
      await screen.findAllByText(saysSo);
      // Everything else the page asks for is answered meanwhile, and none of the family's data
      // shows beside the failure: it would be shown without the warnings.
      await settle(apiMock.get);
      const failure = screen.getAllByText(saysSo)[0].closest('.page-state');
      expectPageStateAlone(container, failure);

      familyAnswer = 'answered';
      fireEvent.click(within(failure as HTMLElement).getByRole('button', { name: 'Retry' }));
      await findImportIncompleteBanner();
      await findAssemblyScopeBanner();
    },
  );

  it.each(FAMILY_ROUTES)(
    '$routePath ($page) shows nothing of the family before its record is in',
    async (route) => {
      const family = held();
      familyAnswer = family.answered;
      const { container } = await renderRoute(route);

      // Everything else the page asks for is answered meanwhile: it still shows a page state
      // alone (loading, or a failure of its own data), never its data without the warnings.
      await settle(apiMock.get);
      expectPageStateAlone(container, container.querySelector('.page-state'));

      family.release();
      await findImportIncompleteBanner();
      await findAssemblyScopeBanner();
    },
  );

  it.each(FAMILY_ROUTES)(
    '$routePath ($page) shows nothing of the family before its project’s reference is in',
    async (route) => {
      // The assembly-scope warning shows nothing while the project list loads (#515): a page
      // that showed its data meanwhile showed it without "Not validated for clinical use",
      // which came a moment later (ENG-12).
      const projects = held();
      projectsAnswer = projects.answered;
      const { container } = await renderRoute(route);

      await settle(apiMock.get);
      expectPageStateAlone(container, container.querySelector('.page-state'));

      projects.release();
      await findImportIncompleteBanner();
      await findAssemblyScopeBanner();
    },
  );
});
