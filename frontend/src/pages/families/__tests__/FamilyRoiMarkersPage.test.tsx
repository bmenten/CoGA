import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import FamilyRoiMarkersPage from '../FamilyRoiMarkersPage';
import api from '../../../lib/api';
import { NUCLEOTIDE_FALLBACK_COLOR } from '../../../lib/phasedMarkers';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../../../test/familyPageBanners';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

const family = {
  family_id: 'co1',
  members: [
    { sample_id: 'FATHER', role: 'father', affected: true, sex: 'male' },
    { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
    { sample_id: 'EMBRYO', role: 'embryo', affected: false, sex: 'female' },
  ],
  roi: { chr: '1', start: 1_000_000, end: 1_001_000, label: 'GENEX', source: 'gene', query: 'GENEX' },
  metadata: { pgt: { inheritance_model: 'AD' } },
};

// The (stub) backend's full set; the mock filters it to the requested window like the
// real endpoint. Two sites sit in the ROI (1,000,000–1,001,000) — one informative, one
// uninformative — one just past it at 1,001,500 (flank, in the window the calls read: the ROI
// and 1 Mb on each side), and one at 2,500,500, beyond that window, revealed by zooming out.
type Marker = { pos: number; hap1: number | null; hap2: number | null };
const ALL_MARKERS: Record<string, Marker[]> = {
  FATHER: [
    { pos: 1_000_500, hap1: 0, hap2: 1 },
    { pos: 1_001_500, hap1: 1, hap2: 0 },
    { pos: 2_500_500, hap1: 1, hap2: 0 },
  ],
  MOTHER: [
    { pos: 1_000_500, hap1: 0, hap2: 0 },
    { pos: 1_001_500, hap1: 1, hap2: 1 },
    { pos: 2_500_500, hap1: 1, hap2: 1 },
  ],
  // 1,000,500: hap1 resolved (informative), hap2 unresolved (null -> greyed).
  EMBRYO: [
    { pos: 1_000_500, hap1: 1, hap2: null },
    { pos: 1_001_500, hap1: 0, hap2: 1 },
    { pos: 2_500_500, hap1: 0, hap2: 1 },
  ],
};
const ALL_SITES = [
  { pos: 1_000_500, ref: 'G', alt: 'A', gts: ['0|1', '0|0', '1|0'] }, // ROI, informative
  { pos: 1_000_800, ref: 'C', alt: 'T', gts: ['0|0', '0|0', '0|0'] }, // ROI, uninformative
  { pos: 1_001_500, ref: 'C', alt: 'T', gts: ['1|0', '1|1', '0|1'] }, // flank, informative
  { pos: 2_500_500, ref: 'C', alt: 'T', gts: ['1|0', '1|1', '0|1'] }, // beyond the window, informative
];
const inWin = (pos: number, start: number, end: number) => pos >= start && pos <= end;
const phasedFor = (start: number, end: number) => ({
  samples: [
    { sample: 'FATHER', markers: ALL_MARKERS.FATHER.filter((m) => inWin(m.pos, start, end)), reference: true, qc: null },
    { sample: 'MOTHER', markers: ALL_MARKERS.MOTHER.filter((m) => inWin(m.pos, start, end)), reference: true, qc: null },
    {
      sample: 'EMBRYO',
      markers: ALL_MARKERS.EMBRYO.filter((m) => inWin(m.pos, start, end)),
      reference: false,
      qc: { informative_sites: 20, mendel_errors: 1, mendel_rate: 0.05 },
    },
  ],
  sites: ALL_SITES.filter((s) => inWin(s.pos, start, end)),
  truncated: false,
});

const hapSeg = (hap1: string, hap2: string, l1: string, l2: string) => [
  { start: 0, end: 2_000_000, hap1, hap2, hap1_lineage: l1, hap2_lineage: l2 },
];
const haplo = {
  samples: [
    { sample: 'FATHER', segments: hapSeg('0', '1', 'paternal', 'paternal') },
    { sample: 'MOTHER', segments: hapSeg('0', '1', 'maternal', 'maternal') },
    { sample: 'EMBRYO', segments: hapSeg('1', '0', 'paternal', 'maternal') },
  ],
};

const renderPage = () =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={['/families/co1/roi-markers']}>
        <Routes>
          <Route path="/families/:familyId/roi-markers" element={<FamilyRoiMarkersPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

// Band cells carry their identity as data attributes (the hover tooltip reads them).
const bandCell = (sample: string, lane: number, pos: number) =>
  document.querySelector<HTMLElement>(
    `[data-sample="${sample}"][data-lane="${lane}"][data-pos="${pos}"]`,
  );

beforeEach(() => {
  (api.get as Mock).mockReset();
  (api.get as Mock).mockImplementation(
    (url: string, config?: { params?: { start?: number; end?: number } }) => {
      if (url === '/families/co1') return Promise.resolve({ data: family });
      if (url.endsWith('/phased-markers')) {
        const { start = 0, end = 0 } = config?.params ?? {};
        return Promise.resolve({ data: phasedFor(start, end) });
      }
      if (url.endsWith('/haplotypes')) return Promise.resolve({ data: haplo });
      return Promise.resolve({ data: {} });
    },
  );
});

describe('FamilyRoiMarkersPage', () => {
  // #607 — a failed request is not an uninformative ROI, nor a family without one.
  describe('when a request fails', () => {
    const failing = (matches: (url: string) => boolean) => {
      const working = (api.get as Mock).getMockImplementation()!;
      (api.get as Mock).mockImplementation((url: string, config?: unknown) =>
        matches(url)
          ? Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }))
          : working(url, config),
      );
    };

    it('says the markers failed to load, not that the ROI holds no informative markers', async () => {
      failing((url) => url.endsWith('/phased-markers'));
      renderPage();

      expect(await screen.findByText(/Could not load the phased markers at the ROI — this is not an empty result/)).toHaveTextContent(
        /Which markers are informative for the embryos is unknown/,
      );
      expect(screen.queryByText(/markers in view/)).not.toBeInTheDocument();
      expect(screen.queryByText(/informative for embryos$/)).not.toBeInTheDocument();
    });

    it('says the haplotype blocks failed to load, instead of leaving the bands uncoloured unsaid', async () => {
      failing((url) => url.endsWith('/haplotypes'));
      renderPage();

      expect(await screen.findByText(/Could not load the haplotype blocks at the ROI/)).toHaveTextContent(
        /The markers are not coloured by the inherited haplotype/,
      );
      // The markers themselves loaded, and are shown.
      expect(screen.getByText(/markers in view/)).toBeInTheDocument();
    });

    it('says the family failed to load, not that it has no region of interest', async () => {
      failing((url) => url === '/families/co1');
      renderPage();

      expect(await screen.findByText('Family could not be loaded')).toBeInTheDocument();
      expect(screen.getByText(/This is a failed request, not a missing family/)).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Retry' })).toBeInTheDocument();
      expect(screen.queryByText('No region of interest')).not.toBeInTheDocument();
    });

    // ENG-10: a family that does not exist, or is not in the user's projects, read "could
    // not be loaded", as a failed request does.
    it('says a family the API does not find is not found', async () => {
      const working = (api.get as Mock).getMockImplementation()!;
      (api.get as Mock).mockImplementation((url: string, config?: unknown) =>
        url === '/families/co1'
          ? Promise.reject(Object.assign(new Error('HTTP 404'), { response: { status: 404, data: { detail: 'Family not found' } } }))
          : working(url, config),
      );
      renderPage();

      expect(await screen.findByRole('heading', { name: 'Family not found' })).toBeInTheDocument();
      expect(screen.queryByText('Family could not be loaded')).not.toBeInTheDocument();
      expect(screen.queryByText('No region of interest')).not.toBeInTheDocument();
    });
  });

  // The markers a PGT call rests on: a partly imported family, or one off the validated
  // scope, is said so under the title, as on every family page.
  it('warns under its title that the import is incomplete and the assembly is not validated', async () => {
    const working = (api.get as Mock).getMockImplementation()!;
    (api.get as Mock).mockImplementation((url: string, config?: unknown) => {
      if (url === '/families/co1') {
        return Promise.resolve({
          data: {
            ...family,
            projects: ['p1'],
            metadata: { ...family.metadata, ...INCOMPLETE_IMPORT_METADATA },
          },
        });
      }
      if (url === '/projects') return Promise.resolve({ data: [offScopeProject('p1')] });
      return working(url, config);
    });
    renderPage();

    const header = (await screen.findByRole('heading', { name: 'ROI marker review — GENEX' })).parentElement;
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
    expect(screen.getByText(/3 markers in view/)).toBeInTheDocument();
  });

  it('opens on the ROI and its 1 Mb flanks with two homolog bands per member and an orange ROI line', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/ROI marker review/)).toBeTruthy());

    // Every family member is shown.
    expect(screen.getByText('FATHER')).toBeTruthy();
    expect(screen.getByText('MOTHER')).toBeTruthy();
    expect(screen.getByText('EMBRYO')).toBeTruthy();

    // Default view is the window the calls and their QC read, the ROI and 1 Mb on each side
    // (0–2,001,000 here): the two ROI sites and the flank site are in view, the site beyond it
    // is not. One ROI site and the flank site are informative for the embryo.
    expect(screen.getByText(/3 markers in view/)).toBeTruthy();
    expect(screen.getByText(/2 informative for embryos/)).toBeTruthy();

    // Two informative sites render -> 3 members × 2 lanes = 6 band rows, × 2 sites = 12 cells.
    expect(document.querySelectorAll('.roi-markers-bandrow').length).toBe(6);
    expect(document.querySelectorAll('.roi-markers-band').length).toBe(12);

    // The allele is linked to its homolog band: father 0|1 -> G on hap1, A on hap2.
    const fatherHap1 = bandCell('FATHER', 1, 1_000_500);
    const fatherHap2 = bandCell('FATHER', 2, 1_000_500);
    expect(fatherHap1?.dataset.base).toBe('G');
    expect(fatherHap2?.dataset.base).toBe('A');
    expect(bandCell('EMBRYO', 1, 1_000_500)?.dataset.base).toBe('A');

    // Informative allele letters are IGV nucleotide colour-coded — G and A get distinct colours.
    const fatherG = fatherHap1?.querySelector('.roi-markers-allele')?.getAttribute('style');
    const fatherA = fatherHap2?.querySelector('.roi-markers-allele')?.getAttribute('style');
    expect(fatherG).toMatch(/color/);
    expect(fatherG).not.toBe(fatherA);

    // The embryo's unresolved hap2 (null marker) is muted: same base G as the father's
    // informative G, but greyed — so the same nucleotide renders differently by lane.
    const embryoHap2 = bandCell('EMBRYO', 2, 1_000_500);
    expect(embryoHap2?.dataset.base).toBe('G');
    expect(embryoHap2?.dataset.informative).toBe('0');
    const mutedG = embryoHap2?.querySelector('.roi-markers-allele');
    expect(mutedG?.className).toContain('roi-markers-allele--muted');
    expect(mutedG?.getAttribute('style')).not.toBe(fatherG);

    // Members are separated by spacer rows (3 members -> 2 gaps); one in-view column is in the
    // ROI, so each gap carries the orange ROI line across that column.
    expect(document.querySelectorAll('.roi-markers-spacer').length).toBe(2);
    expect(document.querySelectorAll('.roi-markers-spacer-cell--roi').length).toBe(2);

    // The uninformative ROI site (1,000,800) and the site beyond the window (2,500,500) are
    // not rendered; the informative ROI site is, and the flank site, dimmed.
    const table = document.querySelector('.roi-markers-table') as HTMLElement;
    expect(table.textContent).not.toContain('1,000,800');
    expect(table.textContent).not.toContain('2,500,500');
    expect(screen.getByText('1,000,500')).toBeTruthy();
    expect(screen.getByTitle(/1,000,500 G>A$/).className).toContain('roi-markers-poscol--roi');
    expect(screen.getByTitle(/1,001,500 .*\(flank\)$/).className).not.toContain('roi-markers-poscol--roi');
    expect(bandCell('EMBRYO', 1, 1_001_500)?.className).toContain('roi-markers-band--flank');

    // The red ⚠ badge after a child's id is the Mendelian-error rate. It uses the
    // chromosome-view floating tooltip (not a native title); hovering it shows the
    // rate and the explanation.
    const badge = screen.getByText(/⚠5\.0%/);
    expect(badge.getAttribute('title')).toBeNull();
    fireEvent.mouseMove(badge, { clientX: 30, clientY: 30 });
    const mendelTip = document.querySelector('.viz-tooltip') as HTMLElement;
    expect(mendelTip).toBeTruthy();
    expect(mendelTip.textContent).toContain('Mendelian error rate: 5.0%');
    expect(mendelTip.textContent).toContain('impossible transmission');

    // Band cells use the same floating tooltip (no native title attribute).
    expect(fatherHap1?.getAttribute('title')).toBeNull();
    fireEvent.mouseMove(fatherHap1 as HTMLElement, { clientX: 20, clientY: 20 });
    const tip = document.querySelector('.viz-tooltip') as HTMLElement;
    expect(tip.textContent).toContain('1,000,500');
    expect(tip.textContent).toContain('FATHER');

    // It is clearly framed as derived, not user-entered.
    expect(screen.getByText(/derived from the phased imputed data/i)).toBeTruthy();
  });

  it('draws an informative allele that is not a single base in the shared grey, not the uninformative one', async () => {
    // The informative site's alt is a two-base allele.
    const working = (api.get as Mock).getMockImplementation()!;
    (api.get as Mock).mockImplementation((url: string, config?: { params?: { start?: number; end?: number } }) => {
      if (!url.endsWith('/phased-markers')) return working(url, config);
      const { start = 0, end = 0 } = config?.params ?? {};
      const data = phasedFor(start, end);
      const sites = data.sites.map((site) => (site.pos === 1_000_500 ? { ...site, alt: 'GT' } : site));
      return Promise.resolve({ data: { ...data, sites } });
    });
    renderPage();
    await waitFor(() => expect(bandCell('FATHER', 2, 1_000_500)).toBeTruthy());

    // jsdom reports an inline colour as rgb(); write the expected ones the same way.
    const asRendered = (color: string) => {
      const probe = document.createElement('span');
      probe.style.color = color;
      return probe.style.color;
    };
    const alleleColor = (sample: string, lane: number) =>
      bandCell(sample, lane, 1_000_500)?.querySelector<HTMLElement>('.roi-markers-allele')?.style.color;

    // The father's informative GT allele was drawn #cbd5e1, the colour of an uninformative
    // allele; it is now the grey the haplotype tooltip uses for it.
    expect(bandCell('FATHER', 2, 1_000_500)?.dataset.base).toBe('GT');
    expect(bandCell('FATHER', 2, 1_000_500)?.dataset.informative).toBe('1');
    expect(alleleColor('FATHER', 2)).toBe(asRendered(NUCLEOTIDE_FALLBACK_COLOR));
    // The embryo's unresolved lane keeps the lighter uninformative grey.
    expect(bandCell('EMBRYO', 2, 1_000_500)?.dataset.informative).toBe('0');
    expect(alleleColor('EMBRYO', 2)).toBe(asRendered('#cbd5e1'));
    expect(alleleColor('FATHER', 2)).not.toBe(alleleColor('EMBRYO', 2));
  });

  it('zooms out to reveal — and dim — the markers beyond the window, then resets to it', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/ROI marker review/)).toBeTruthy());

    const phasedCall = (start: number, end: number) =>
      (api.get as Mock).mock.calls.some(
        ([url, opts]) =>
          typeof url === 'string' &&
          url.endsWith('/phased-markers') &&
          opts?.params?.start === start &&
          opts?.params?.end === end,
      );

    // Standard view opens on the ROI (1,000,000–1,001,000) and 1 Mb on each side, the chromosome
    // start bounding it on the left.
    expect(phasedCall(0, 2_001_000)).toBe(true);

    // Zoom out doubles the span about the centre (1,000,500), which now reaches the site at
    // 2,500,500.
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    await waitFor(() => expect(phasedCall(0, 3_001_500)).toBe(true));

    // That marker now appears, tagged as flank (not ROI) and dimmed; the ROI column stays
    // highlighted.
    const farHeader = await screen.findByTitle(/2,500,500 .*\(flank\)$/);
    expect(farHeader.className).not.toContain('roi-markers-poscol--roi');
    expect(screen.getByTitle(/1,000,500 G>A$/).className).toContain('roi-markers-poscol--roi');
    expect(bandCell('EMBRYO', 1, 2_500_500)?.className).toContain('roi-markers-band--flank');
    expect(bandCell('EMBRYO', 1, 1_000_500)?.className).not.toContain('roi-markers-band--flank');

    // Reset returns to the window the calls read.
    fireEvent.click(screen.getByRole('button', { name: /Reset/ }));
    await waitFor(() =>
      expect((api.get as Mock).mock.calls.filter(([url]) => String(url).endsWith('/phased-markers')).length)
        .toBeGreaterThan(2),
    );
    await waitFor(() => expect(screen.queryByTitle(/2,500,500 .*\(flank\)$/)).toBeNull());
  });

  it('keeps the informative markers nearest the ROI when the window holds more than it draws', async () => {
    // 450 informative sites left of the ROI (100,000–549,000) and 450 right of it
    // (1,450,000–1,899,000), all in the window the calls read: 900, of which 800 are drawn.
    const positions = [
      ...Array.from({ length: 450 }, (_, index) => 100_000 + index * 1_000),
      ...Array.from({ length: 450 }, (_, index) => 1_450_000 + index * 1_000),
    ];
    const dense = {
      samples: [
        { sample: 'FATHER', markers: positions.map((pos) => ({ pos, hap1: 0, hap2: 1 })), reference: true, qc: null },
        { sample: 'MOTHER', markers: positions.map((pos) => ({ pos, hap1: 0, hap2: 0 })), reference: true, qc: null },
        {
          sample: 'EMBRYO',
          markers: positions.map((pos) => ({ pos, hap1: 1, hap2: null })),
          reference: false,
          qc: { informative_sites: 900, mendel_errors: 0, mendel_rate: 0 },
        },
      ],
      sites: positions.map((pos) => ({ pos, ref: 'G', alt: 'A', gts: ['0|1', '0|0', '1|0'] })),
      truncated: false,
    };
    const working = (api.get as Mock).getMockImplementation()!;
    (api.get as Mock).mockImplementation((url: string, config?: unknown) =>
      url.endsWith('/phased-markers') ? Promise.resolve({ data: dense }) : working(url, config),
    );
    renderPage();

    expect(
      await screen.findByText(/Showing the 800 of 900 informative markers nearest the ROI; pan or zoom in to see the others/),
    ).toBeInTheDocument();
    // The farthest on each side are left out, not the right flank as a whole.
    expect(bandCell('EMBRYO', 1, 549_000)).not.toBeNull();
    expect(bandCell('EMBRYO', 1, 1_450_000)).not.toBeNull();
    expect(bandCell('EMBRYO', 1, 100_000)).toBeNull();
    expect(bandCell('EMBRYO', 1, 1_899_000)).toBeNull();
    expect(document.querySelectorAll('[data-sample="EMBRYO"][data-lane="1"]').length).toBe(800);
  });

  it('highlights Mendelian-error markers on the child with a light-orange cell', async () => {
    const familyWithRel = {
      ...family,
      relationships: [
        { id: 'r1', relationship_type: 'parent_child', sample_id_a: 'FATHER', sample_id_b: 'EMBRYO', role_a: 'father' },
        { id: 'r2', relationship_type: 'parent_child', sample_id_a: 'MOTHER', sample_id_b: 'EMBRYO', role_a: 'mother' },
      ],
    };
    // EMBRYO 1|1 from two 0|0 parents is an impossible transmission.
    const phasedErr = {
      samples: [
        { sample: 'FATHER', markers: [{ pos: 1_000_500, hap1: 0, hap2: 0 }], reference: true, qc: null },
        { sample: 'MOTHER', markers: [{ pos: 1_000_500, hap1: 0, hap2: 0 }], reference: true, qc: null },
        {
          sample: 'EMBRYO',
          markers: [{ pos: 1_000_500, hap1: 1, hap2: 1 }],
          reference: false,
          qc: { informative_sites: 10, mendel_errors: 1, mendel_rate: 0.1 },
        },
      ],
      sites: [{ pos: 1_000_500, ref: 'G', alt: 'A', gts: ['0|0', '0|0', '1|1'] }],
      truncated: false,
    };
    (api.get as Mock).mockImplementation((url: string) => {
      if (url === '/families/co1') return Promise.resolve({ data: familyWithRel });
      if (url.endsWith('/phased-markers')) return Promise.resolve({ data: phasedErr });
      if (url.endsWith('/haplotypes')) return Promise.resolve({ data: haplo });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await waitFor(() => expect(screen.getByText(/ROI marker review/)).toBeTruthy());

    // Both of the child's lane cells at the impossible site are flagged; parents are not.
    expect(bandCell('EMBRYO', 1, 1_000_500)?.className).toContain('roi-markers-band--mendel');
    expect(bandCell('EMBRYO', 2, 1_000_500)?.className).toContain('roi-markers-band--mendel');
    expect(bandCell('FATHER', 1, 1_000_500)?.className).not.toContain('roi-markers-band--mendel');

    // The cell tooltip explains it.
    fireEvent.mouseMove(bandCell('EMBRYO', 1, 1_000_500) as HTMLElement, { clientX: 25, clientY: 25 });
    expect((document.querySelector('.viz-tooltip') as HTMLElement).textContent).toContain('Mendelian error');
  });

  it('shades block-mismatch markers orange and homozygous markers light grey', async () => {
    // laneColor reads CSS variables; jsdom needs them set so the two founder shades differ.
    const root = document.documentElement;
    root.style.setProperty('--color-haplotype-father-dark', '#13386b');
    root.style.setProperty('--color-haplotype-father-light', '#6f9ad6');
    root.style.setProperty('--color-haplotype-mother-dark', '#14532d');
    root.style.setProperty('--color-haplotype-mother-light', '#6fae7f');
    root.style.setProperty('--color-haplotype-unknown', '#9ca3af');

    const phasedData = {
      samples: [
        // FATHER 1|1 is homozygous and uninformative (null lanes).
        { sample: 'FATHER', markers: [{ pos: 1_000_500, hap1: null, hap2: null }], reference: true, qc: null },
        { sample: 'MOTHER', markers: [{ pos: 1_000_500, hap1: 0, hap2: 1 }], reference: true, qc: null },
        // EMBRYO hap1 marker = founder homolog 1, but its block (below) says 0 → disagreement.
        { sample: 'EMBRYO', markers: [{ pos: 1_000_500, hap1: 1, hap2: null }], reference: false, qc: null },
      ],
      sites: [{ pos: 1_000_500, ref: 'G', alt: 'A', gts: ['1|1', '0|1', '1|0'] }],
      truncated: false,
    };
    const haploData = {
      samples: [
        { sample: 'FATHER', segments: hapSeg('0', '1', 'paternal', 'paternal') },
        { sample: 'MOTHER', segments: hapSeg('0', '1', 'maternal', 'maternal') },
        { sample: 'EMBRYO', segments: hapSeg('0', '0', 'paternal', 'maternal') },
      ],
    };
    (api.get as Mock).mockImplementation((url: string) => {
      if (url === '/families/co1') return Promise.resolve({ data: family });
      if (url.endsWith('/phased-markers')) return Promise.resolve({ data: phasedData });
      if (url.endsWith('/haplotypes')) return Promise.resolve({ data: haploData });
      return Promise.resolve({ data: {} });
    });

    renderPage();
    await waitFor(() => expect(screen.getByText(/ROI marker review/)).toBeTruthy());

    // Homozygous FATHER (1|1) → faint grey on both lanes.
    expect(bandCell('FATHER', 1, 1_000_500)?.className).toContain('roi-markers-band--homozygous');
    expect(bandCell('FATHER', 2, 1_000_500)?.className).toContain('roi-markers-band--homozygous');
    // EMBRYO hap1 disagrees with its block → orange (and not the grey).
    const embryoHap1 = bandCell('EMBRYO', 1, 1_000_500);
    expect(embryoHap1?.className).toContain('roi-markers-band--block-mismatch');
    expect(embryoHap1?.className).not.toContain('roi-markers-band--homozygous');

    root.style.removeProperty('--color-haplotype-father-dark');
    root.style.removeProperty('--color-haplotype-father-light');
    root.style.removeProperty('--color-haplotype-mother-dark');
    root.style.removeProperty('--color-haplotype-mother-light');
    root.style.removeProperty('--color-haplotype-unknown');
  });

  // CLIN-60: on chrX outside the pseudo-autosomal regions a man has one X. The QC counts read
  // him as one copy, and the per-cell check reads him the same way.
  describe('at an X-linked ROI', () => {
    const members = ['FATHER', 'MOTHER', 'SON', 'DAUGHTER', 'UNRECORDED'];
    const xFamily = {
      ...family,
      members: [
        { sample_id: 'FATHER', role: 'father', affected: false, sex: 'male' },
        { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
        { sample_id: 'SON', role: 'embryo', affected: false, sex: 'male' },
        { sample_id: 'DAUGHTER', role: 'embryo', affected: false, sex: 'female' },
        { sample_id: 'UNRECORDED', role: 'embryo', affected: false, sex: 'und' },
      ],
      relationships: ['SON', 'DAUGHTER', 'UNRECORDED'].flatMap((child) => [
        { id: `f-${child}`, relationship_type: 'parent_child', sample_id_a: 'FATHER', sample_id_b: child, role_a: 'father' },
        { id: `m-${child}`, relationship_type: 'parent_child', sample_id_a: 'MOTHER', sample_id_b: child, role_a: 'mother' },
      ]),
      roi: { chr: 'X', start: 31_000_000, end: 31_001_000, label: 'DMD', source: 'gene', query: 'DMD' },
      metadata: { pgt: { inheritance_model: 'XLR' } },
    };
    // Genotypes per site, in member order. The men's calls are written as one allele or twice.
    const xSites = [
      // The son carries his mother's homolog 1, the daughter her father's 0 and her mother's 1;
      // read as two copies, the child whose sex is not recorded cannot have 1|1 from a 0 father.
      { pos: 31_000_100, gts: ['0', '0|1', '1', '0|1', '1|1'] },
      // Written twice, the son's one X: no error, though his father's allele is not his.
      { pos: 31_000_200, gts: ['0|0', '0|1', '1|1', '0|0', '0|1'] },
      // Two different alleles in the son; the daughter lacks her father's one allele.
      { pos: 31_000_300, gts: ['1', '0|0', '0|1', '0|0', '0|0'] },
      // The son's allele is his mother's, not his father's: a father-son pair is not checked.
      { pos: 31_000_400, gts: ['1|1', '0|0', '0', '1|0', '1|0'] },
    ];
    type QcCounts = { informative_sites: number; mendel_errors: number; mendel_rate: number };
    const xPhased = (ranges: number[][], sites = xSites, qc: Record<string, QcCounts> = {}) => ({
      samples: members.map((sample) => ({
        sample,
        // Each embryo has a marker at each site, so the review shows every site.
        markers: ['FATHER', 'MOTHER'].includes(sample) ? [] : sites.map((site) => ({ pos: site.pos, hap1: null, hap2: 1 })),
        reference: ['FATHER', 'MOTHER'].includes(sample),
        qc: qc[sample] ?? null,
      })),
      sites: sites.map((site) => ({ ...site, ref: 'A', alt: 'G' })),
      truncated: false,
      hemizygous_x_ranges: ranges,
    });
    const mockX = (ranges: number[][], sites = xSites, qc: Record<string, QcCounts> = {}) =>
      (api.get as Mock).mockImplementation((url: string) => {
        if (url === '/families/co1') return Promise.resolve({ data: xFamily });
        if (url.endsWith('/phased-markers')) return Promise.resolve({ data: xPhased(ranges, sites, qc) });
        if (url.endsWith('/haplotypes')) return Promise.resolve({ data: { samples: [] } });
        return Promise.resolve({ data: {} });
      });
    const flagged = (sample: string) =>
      xSites.map((site) => site.pos).filter((pos) => bandCell(sample, 1, pos)?.dataset.mendel === '1');
    // The allele drawn on each of a member's two homolog rows at a site.
    const rowBases = (sample: string, pos: number) => [1, 2].map((lane) => bandCell(sample, lane, pos)?.dataset.base);
    const tooltipOver = (element: HTMLElement | null) => {
      fireEvent.mouseMove(element as HTMLElement, { clientX: 10, clientY: 10 });
      return (document.querySelector('.viz-tooltip') as HTMLElement | null)?.textContent ?? '';
    };

    it('checks a son on his mother X alone and a daughter against her father one X', async () => {
      mockX([[31_000_000, 31_001_000]]);
      renderPage();
      await waitFor(() => expect(bandCell('SON', 1, 31_000_100)).toBeTruthy());

      expect(flagged('SON')).toEqual([31_000_300]);
      expect(flagged('DAUGHTER')).toEqual([31_000_300]);
      // A member whose sex is not recorded is read as two copies, as before.
      expect(flagged('UNRECORDED')).toEqual([31_000_100, 31_000_300]);
      expect(flagged('FATHER')).toEqual([]);
    });

    it('reads every member as two copies where a man is not known to have one X', async () => {
      // In a PAR, or on an assembly whose PAR bounds are not known, the backend serves no range.
      mockX([]);
      renderPage();
      await waitFor(() => expect(bandCell('SON', 1, 31_000_100)).toBeTruthy());

      // The son as two copies: 1|1 from a 0|0 father, 0|1 from a 0|0 mother. Calls written as
      // one allele are not read.
      expect(flagged('SON')).toEqual([31_000_200]);
      expect(flagged('DAUGHTER')).toEqual([]);
      expect(flagged('UNRECORDED')).toEqual([]);
    });

    // ENG-19: a call of one allele showed as '·' on both rows, as if the genotype were missing.
    it('draws a man’s one X as its one allele, on the row of his X, and no allele on the other', async () => {
      mockX([[31_000_000, 31_001_000]]);
      renderPage();
      await waitFor(() => expect(bandCell('SON', 1, 31_000_100)).toBeTruthy());

      // Written once: the father's one X on his first row, a son's, his mother's X, on his
      // second, the maternal one.
      expect(rowBases('FATHER', 31_000_100)).toEqual(['A', '–']);
      expect(rowBases('SON', 31_000_100)).toEqual(['–', 'G']);
      // Written twice, a man's call is the same one allele, as the QC reads it: not two
      // copies, so not shaded as homozygous.
      expect(rowBases('FATHER', 31_000_200)).toEqual(['A', '–']);
      expect(rowBases('SON', 31_000_200)).toEqual(['–', 'G']);
      expect(bandCell('SON', 2, 31_000_200)?.className).not.toContain('roi-markers-band--homozygous');
      // The son's allele is on his informative maternal row; the row without one is muted.
      expect(bandCell('SON', 2, 31_000_100)?.dataset.informative).toBe('1');
      expect(bandCell('SON', 1, 31_000_100)?.dataset.informative).toBe('0');
      // Two copies: a woman, a member whose sex is not recorded, and a son's call of two
      // different alleles, drawn as called.
      expect(rowBases('MOTHER', 31_000_100)).toEqual(['A', 'G']);
      expect(rowBases('UNRECORDED', 31_000_100)).toEqual(['G', 'G']);
      expect(rowBases('DAUGHTER', 31_000_200)).toEqual(['A', 'A']);
      expect(bandCell('DAUGHTER', 1, 31_000_200)?.className).toContain('roi-markers-band--homozygous');
      expect(rowBases('SON', 31_000_300)).toEqual(['A', 'G']);
      // No call of one allele reads as a missing one.
      expect(document.querySelectorAll('.roi-markers-band[data-base="·"]')).toHaveLength(0);
      // The row without an allele says why.
      expect(tooltipOver(bandCell('SON', 1, 31_000_100))).toContain('no allele: one copy here');
    });

    it('draws a call written as one allele as that allele where a man is not known to have one X', async () => {
      mockX([]);
      renderPage();
      await waitFor(() => expect(bandCell('SON', 1, 31_000_100)).toBeTruthy());

      // The call is what it is, though the QC does not read it there.
      expect(rowBases('FATHER', 31_000_100)).toEqual(['A', '–']);
      expect(rowBases('SON', 31_000_100)).toEqual(['–', 'G']);
      // Written twice, it is two copies there.
      expect(rowBases('SON', 31_000_200)).toEqual(['G', 'G']);
      expect(document.querySelectorAll('.roi-markers-band[data-base="·"]')).toHaveLength(0);
    });

    // ENG-19: the note said the son's genotype "can't be inherited from the parents", as if his
    // father gave him an X.
    it('says a son’s Mendelian error on his one X is against his mother alone', async () => {
      const sites = [
        // A son's call of two different alleles; the daughter lacks her father's one allele.
        { pos: 31_000_300, gts: ['1', '0|0', '0|1', '0|0', '0|0'] },
        // The son's allele is one his mother does not carry.
        { pos: 31_000_500, gts: ['1', '0|0', '1', '1|0', '1|0'] },
      ];
      mockX([[31_000_000, 31_001_000]], sites, { SON: { informative_sites: 2, mendel_errors: 2, mendel_rate: 1 } });
      renderPage();
      await waitFor(() => expect(bandCell('SON', 1, 31_000_300)).toBeTruthy());
      expect(bandCell('SON', 2, 31_000_500)?.dataset.mendel).toBe('1');
      expect(bandCell('SON', 1, 31_000_300)?.dataset.mendel).toBe('1');

      const notCarried = tooltipOver(bandCell('SON', 2, 31_000_500));
      expect(notCarried).toContain('his mother does not carry this allele');
      expect(notCarried).not.toContain('inherited from the parents');
      const twoAlleles = tooltipOver(bandCell('SON', 1, 31_000_300));
      expect(twoAlleles).toContain('two different alleles, which a son cannot have on his one X');
      expect(twoAlleles).not.toContain('inherited from the parents');
      // A daughter has two copies, one from each parent: the note is unchanged.
      expect(tooltipOver(bandCell('DAUGHTER', 1, 31_000_300))).toContain(
        "this genotype can't be inherited from the parents",
      );

      // The rate's note says so too.
      const rate = tooltipOver(screen.getByText(/⚠100\.0%/));
      expect(rate).toContain('Mendelian error rate: 100.0%');
      expect(rate).toContain('On chrX outside the PARs a son’s one X is his mother’s: there he is checked against her alone.');
      expect(rate).not.toContain('both parents');
    });
  });
});
