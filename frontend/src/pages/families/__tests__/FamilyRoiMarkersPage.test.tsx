import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import FamilyRoiMarkersPage from '../FamilyRoiMarkersPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

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
// uninformative — and one just past it at 1,001,500 (flank), revealed by zooming out.
type Marker = { pos: number; hap1: number | null; hap2: number | null };
const ALL_MARKERS: Record<string, Marker[]> = {
  FATHER: [
    { pos: 1_000_500, hap1: 0, hap2: 1 },
    { pos: 1_001_500, hap1: 1, hap2: 0 },
  ],
  MOTHER: [
    { pos: 1_000_500, hap1: 0, hap2: 0 },
    { pos: 1_001_500, hap1: 1, hap2: 1 },
  ],
  // 1,000,500: hap1 resolved (informative), hap2 unresolved (null -> greyed).
  EMBRYO: [
    { pos: 1_000_500, hap1: 1, hap2: null },
    { pos: 1_001_500, hap1: 0, hap2: 1 },
  ],
};
const ALL_SITES = [
  { pos: 1_000_500, ref: 'G', alt: 'A', gts: ['0|1', '0|0', '1|0'] }, // ROI, informative
  { pos: 1_000_800, ref: 'C', alt: 'T', gts: ['0|0', '0|0', '0|0'] }, // ROI, uninformative
  { pos: 1_001_500, ref: 'C', alt: 'T', gts: ['1|0', '1|1', '0|1'] }, // flank, informative
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
  it('opens on the ROI with two homolog bands per member and an orange ROI line', async () => {
    renderPage();
    await waitFor(() => expect(screen.getByText(/ROI marker review/)).toBeTruthy());

    // Every family member is shown.
    expect(screen.getByText('FATHER')).toBeTruthy();
    expect(screen.getByText('MOTHER')).toBeTruthy();
    expect(screen.getByText('EMBRYO')).toBeTruthy();

    // Default view is the ROI only: the two ROI sites are in view, the flank site is not.
    // Of the ROI sites, one is informative for the embryo.
    expect(screen.getByText(/2 markers in view/)).toBeTruthy();
    expect(screen.getByText(/1 informative for embryos/)).toBeTruthy();

    // One informative site renders -> 3 members × 2 lanes × 1 site = 6 band cells.
    expect(document.querySelectorAll('.roi-markers-bandrow').length).toBe(6);
    expect(document.querySelectorAll('.roi-markers-band').length).toBe(6);

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

    // Members are separated by spacer rows (3 members -> 2 gaps), and every in-view column
    // is in the ROI, so each gap carries the orange ROI line across its one column.
    expect(document.querySelectorAll('.roi-markers-spacer').length).toBe(2);
    expect(document.querySelectorAll('.roi-markers-spacer-cell--roi').length).toBe(2);

    // The uninformative ROI site (1,000,800) and the out-of-window flank site (1,001,500)
    // are not rendered; the informative ROI site is.
    const table = document.querySelector('.roi-markers-table') as HTMLElement;
    expect(table.textContent).not.toContain('1,000,800');
    expect(table.textContent).not.toContain('1,001,500');
    expect(screen.getByText('1,000,500')).toBeTruthy();
    expect(screen.getByTitle(/1,000,500 G>A$/).className).toContain('roi-markers-poscol--roi');

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

  it('zooms out to reveal — and dim — the flanking markers, then resets to the ROI', async () => {
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

    // Standard view opens on the ROI itself (1,000,000–1,001,000).
    expect(phasedCall(1_000_000, 1_001_000)).toBe(true);

    // Zoom out doubles the span about the centre (1,000,500); the 1 kb ROI -> 2 kb window,
    // which now reaches the flank site at 1,001,500.
    fireEvent.click(screen.getByRole('button', { name: 'Zoom out' }));
    await waitFor(() => expect(phasedCall(999_500, 1_001_500)).toBe(true));

    // The flank marker now appears, tagged as flank (not ROI) and dimmed; the ROI column
    // stays highlighted.
    const flankHeader = await screen.findByTitle(/1,001,500 .*\(flank\)$/);
    expect(flankHeader.className).not.toContain('roi-markers-poscol--roi');
    expect(screen.getByTitle(/1,000,500 G>A$/).className).toContain('roi-markers-poscol--roi');
    const flankCell = bandCell('EMBRYO', 1, 1_001_500);
    expect(flankCell?.className).toContain('roi-markers-band--flank');
    expect(bandCell('EMBRYO', 1, 1_000_500)?.className).not.toContain('roi-markers-band--flank');

    // Reset returns to the ROI-only window.
    fireEvent.click(screen.getByRole('button', { name: /Reset/ }));
    await waitFor(() =>
      expect((api.get as Mock).mock.calls.filter(([url]) => String(url).endsWith('/phased-markers')).length)
        .toBeGreaterThan(2),
    );
    await waitFor(() => expect(screen.queryByTitle(/1,001,500 .*\(flank\)$/)).toBeNull());
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
});
