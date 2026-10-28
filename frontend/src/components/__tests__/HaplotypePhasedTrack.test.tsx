import { fireEvent, render, within } from '@testing-library/react';
import { beforeEach, describe, expect, test, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock, keepPreviousData: (previous: unknown) => previous }));
vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import HaplotypePhasedTrack from '../visualizations/HaplotypePhasedTrack';
import { NUCLEOTIDE_FALLBACK_COLOR } from '../../lib/phasedMarkers';

const members = [
  { sample_id: 'FATHER', role: 'father' },
  { sample_id: 'MOTHER', role: 'mother' },
  { sample_id: 'CHILD', role: 'proband', affected: false },
  // A relative (e.g. paternal grandmother): rendered for context but carries no
  // inheritance markers of its own.
  { sample_id: 'RELATIVE', role: 'grandmother', affected: true },
];

const segments = [
  { start: 0, end: 500, hap1: '0', hap2: '1', ps: 1 },
  { start: 500, end: 1000, hap1: '1', hap2: '1', ps: 1 },
];

// Child inherited the paternal homolog; maternal side uninformative.
const childMarkers = [
  { pos: 100, hap1: 0, hap2: null },
  { pos: 300, hap1: 1, hap2: null },
];
const fatherMarkers = [
  { pos: 100, hap1: 0, hap2: 1 },
  { pos: 300, hap1: 0, hap2: 1 },
];
const motherMarkers = [
  { pos: 100, hap1: 0, hap2: 0 },
  { pos: 300, hap1: 0, hap2: 0 },
];

type Site = { pos: number; ref: string; alt: string; gts: string[] };

const mockData = (opts: { segments?: typeof segments; truncated?: boolean; sites?: Site[] }) => {
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => {
    if (queryKey[0] === 'phased-markers') {
      // A truncated fetch returns no markers/sites (the server suppresses the
      // partial overlay) and flags truncated so the client shows a zoom-in hint.
      if (opts.truncated) {
        return {
          data: {
            samples: [
              { sample: 'FATHER', markers: [], reference: true },
              { sample: 'MOTHER', markers: [], reference: true },
              { sample: 'CHILD', markers: [] },
            ],
            sites: [],
            truncated: true,
            covered: [1000, 5000],
          },
        };
      }
      return {
        data: {
          samples: [
            { sample: 'FATHER', markers: fatherMarkers, reference: true },
            { sample: 'MOTHER', markers: motherMarkers, reference: true },
            { sample: 'CHILD', markers: childMarkers },
            // The relative is listed (so the tooltip can label its lane) but, like a
            // real relative, carries no inheritance markers of its own.
            { sample: 'RELATIVE', markers: [] },
          ],
          // gts aligned to samples order [FATHER, MOTHER, CHILD, RELATIVE]; ref G / alt A.
          sites: opts.sites ?? [
            { pos: 100, ref: 'G', alt: 'A', gts: ['0|1', '0|0', '0|0', '1|0'] },
            { pos: 300, ref: 'C', alt: 'T', gts: ['0|1', '0|0', '0|1', '0|1'] },
          ],
        },
      };
    }
    if (queryKey[0] === 'haplotypes') {
      // Segments for whichever member's track is rendered (the relative gets the
      // same shape so it has a hoverable, non-empty block track).
      const samples = ['CHILD', 'RELATIVE'].map((sample) => ({
        sample,
        segments: opts.segments ?? [],
      }));
      return {
        data: { chr: '1', start: 0, end: 1000, samples },
        isLoading: false,
      };
    }
    return { data: undefined, isLoading: false };
  });
};

const renderTrack = (showMarkers: boolean, sampleId = 'CHILD') => {
  const member = members.find((m) => m.sample_id === sampleId);
  return render(
    <HaplotypePhasedTrack
      familyId="F1"
      sampleId={sampleId}
      chrom="1"
      regionStart={0}
      regionEnd={1000}
      width={500}
      height={36}
      role={member?.role ?? 'proband'}
      affected={member?.affected ?? false}
      sex="male"
      highlightRiskHaplotype={false}
      familyMembers={members}
      inheritanceModel="AD"
      riskRegion={null}
      showMarkers={showMarkers}
    />,
  );
};

beforeEach(() => {
  useQueryMock.mockReset();
});

test('shows an empty message when there are no haplotype segments', () => {
  mockData({ segments: [] });
  const { container } = renderTrack(false);
  expect(container.textContent).toContain('No haplotype data in this region');
});

test('marker tooltip shows colour-coded nucleotide genotypes for all members', () => {
  mockData({ segments });
  const { container } = renderTrack(true);
  const canvas = container.querySelector('canvas') as Element;
  // x = 50 over width 500 across region 0–1000 → genomic pos 100 (the first marker).
  fireEvent.mouseMove(canvas, { clientX: 50, clientY: 10 });
  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip?.textContent).toContain('chr1:100');
  // ref›alt and every family member, with phased genotypes decoded to nucleotides.
  expect(tooltip?.textContent).toContain('G›A');
  expect(tooltip?.textContent).toContain('FATHER');
  expect(tooltip?.textContent).toContain('MOTHER');
  expect(tooltip?.textContent).toContain('CHILD');
  expect(tooltip?.textContent).toContain('G | A'); // father 0|1
  expect(tooltip?.textContent).toContain('G | G'); // mother 0|0
  // The child's raw marker (hap1 = 0) agrees with its block (hap1 = '0') here.
  expect(tooltip?.textContent).toContain('✓');
});

// The marker tooltip wrote the chromosome as the route holds it (`1:100`), where the viewer names
// it chr1 (#602), as the genome overview's SV tooltip does since ENG-20 (found while fixing
// ENG-20, #1030).
test('marker tooltip names the chromosome as the viewer does', () => {
  mockData({ segments });
  const { container } = renderTrack(true);

  fireEvent.mouseMove(container.querySelector('canvas') as Element, { clientX: 50, clientY: 10 });

  const tooltip = document.body.querySelector('.viz-tooltip') as HTMLElement;
  expect(within(tooltip).getByText('chr1:100')).toBeInTheDocument();
});

test('marker tooltip draws an allele that is not a single base in the shared grey', () => {
  mockData({
    segments,
    sites: [
      { pos: 100, ref: 'N', alt: 'GTT', gts: ['0|1', '0|0', '0|0', '1|0'] },
      { pos: 300, ref: 'C', alt: 'T', gts: ['0|1', '0|0', '0|1', '0|1'] },
    ],
  });
  const { container } = renderTrack(true);
  fireEvent.mouseMove(container.querySelector('canvas') as Element, { clientX: 50, clientY: 10 });
  const tooltip = document.body.querySelector('.viz-tooltip') as HTMLElement;
  expect(tooltip.textContent).toContain('N›GTT');

  // jsdom reports an inline colour as rgb(); write the expected one the same way.
  const probe = document.createElement('span');
  probe.style.color = NUCLEOTIDE_FALLBACK_COLOR;
  const letterColors = (text: string) =>
    [...tooltip.querySelectorAll<HTMLElement>('span')]
      .filter((span) => span.textContent === text && span.children.length === 0)
      .map((span) => span.style.color);
  // A multi-base allele was drawn #cbd5e1 and an N #9ca3af: both are now the one grey,
  // the grey the ROI marker table uses.
  expect(letterColors('GTT').length).toBeGreaterThan(0);
  expect(new Set(letterColors('GTT'))).toEqual(new Set([probe.style.color]));
  expect(new Set(letterColors('N'))).toEqual(new Set([probe.style.color]));
});

test('relative track with no own markers still shows the tooltip on hover near a site', () => {
  mockData({ segments });
  // RELATIVE carries no inheritance markers of its own, yet hovering near a site
  // (pos 100 -> x = 50) must still surface the all-member genotype table by
  // anchoring on the union of all members' marker positions.
  const { container } = renderTrack(true, 'RELATIVE');
  const canvas = container.querySelector('canvas') as Element;
  fireEvent.mouseMove(canvas, { clientX: 50, clientY: 10 });
  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip).not.toBeNull();
  expect(tooltip?.textContent).toContain('chr1:100');
  expect(tooltip?.textContent).toContain('FATHER');
  expect(tooltip?.textContent).toContain('CHILD');
  expect(tooltip?.textContent).toContain('RELATIVE');
  // The relative's own phased genotype at pos 100 (1|0 over ref G / alt A) is shown.
  expect(tooltip?.textContent).toContain('A | G');
});

test('shows a zoom-in hint and no marker tooltip when the fetch is truncated', () => {
  mockData({ segments, truncated: true });
  const { container } = renderTrack(true);
  // The unobtrusive 'too many sites' hint is shown (blocks still render).
  expect(container.textContent).toContain('too many sites');
  // With markers suppressed, hovering must not produce a marker tooltip.
  const canvas = container.querySelector('canvas') as Element;
  fireEvent.mouseMove(canvas, { clientX: 50, clientY: 10 });
  expect(document.body.querySelector('.viz-tooltip')).toBeNull();
});

test('does not show the zoom-in hint when the fetch is not truncated', () => {
  mockData({ segments });
  const { container } = renderTrack(true);
  expect(container.textContent).not.toContain('too many sites');
});

test('does not fetch phased markers when the overlay is off', () => {
  mockData({ segments });
  renderTrack(false);
  const phasedCall = useQueryMock.mock.calls.find(
    (call) => (call[0] as { queryKey: unknown[] }).queryKey[0] === 'phased-markers',
  );
  expect((phasedCall?.[0] as { enabled?: boolean } | undefined)?.enabled).toBe(false);
});

test('a failed haplotype request shows the failure, not "no haplotype data" or a risk state (#510)', () => {
  const refetch = vi.fn();
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'haplotypes'
      ? { data: undefined, isLoading: false, isError: true, refetch }
      : { data: undefined, isLoading: false, isError: false },
  );
  const { container, getByRole } = renderTrack(false);

  expect(container.textContent).toContain('Could not load haplotypes — this is not an empty result');
  expect(container.textContent).not.toContain('No haplotype data in this region');
  expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('unavailable');
  fireEvent.click(getByRole('button', { name: 'Retry' }));
  expect(refetch).toHaveBeenCalled();
});

test('a failed marker request is flagged while the blocks stay drawn (#510)', () => {
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'phased-markers'
      ? { data: undefined, isLoading: false, isError: true }
      : {
          data: { samples: members.map((m) => ({ sample: m.sample_id, segments })) },
          isLoading: false,
          isError: false,
        },
  );
  const { container } = renderTrack(true);

  expect(container.textContent).toContain('Phased markers could not load');
  expect(container.textContent).not.toContain('Could not load haplotypes');
});

// #529: the canvas is a named image — whose haplotypes, where, and the risk state its
// border shows.
test('is named with whose haplotypes, where, and the risk state at the ROI (#529)', () => {
  const trio = [
    { sample_id: 'FATHER', role: 'father', affected: true },
    { sample_id: 'MOTHER', role: 'mother', affected: false },
    { sample_id: 'CHILD', role: 'proband', affected: true },
  ];
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'haplotypes'
      ? {
          data: {
            chr: '1',
            start: 0,
            end: 1000,
            samples: ['FATHER', 'CHILD'].map((sample) => ({
              sample,
              segments: [{ start: 0, end: 1000, hap1: '0', hap2: '1', ps: 1 }],
            })),
          },
          isLoading: false,
        }
      : { data: undefined, isLoading: false },
  );
  const { getByRole } = render(
    <HaplotypePhasedTrack
      familyId="F1"
      sampleId="CHILD"
      chrom="1"
      regionStart={0}
      regionEnd={1000}
      width={500}
      height={36}
      role="proband"
      affected
      sex="male"
      highlightRiskHaplotype
      familyMembers={trio}
      inheritanceModel="AD"
      riskRegion={{ chr: '1', start: 400, end: 600 }}
    />,
  );

  // The affected father and child share only his first homolog, which the child carries.
  expect(
    getByRole('img', {
      name: 'Haplotypes of CHILD on chr1:0–1,000; risk state at chr1:400–600: affected / at risk',
    }),
  ).toBeInTheDocument();
});

test('a loading, empty or failed track claims no risk state it does not have (#529, #510)', () => {
  useQueryMock.mockImplementation(() => ({ data: undefined, isLoading: true }));
  const loading = renderTrack(false);
  expect(loading.getByRole('img', { name: 'Haplotypes of CHILD on chr1:0–1,000: loading' })).toBeInTheDocument();
  loading.unmount();

  mockData({ segments: [] });
  const empty = renderTrack(false);
  expect(
    empty.getByRole('img', {
      name: 'Haplotypes of CHILD on chr1:0–1,000: no data; risk state: uninformative',
    }),
  ).toBeInTheDocument();
  empty.unmount();

  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'haplotypes'
      ? { data: undefined, isLoading: false, isError: true, refetch: vi.fn() }
      : { data: undefined, isLoading: false, isError: false },
  );
  const { getByRole } = renderTrack(false);
  expect(
    getByRole('img', { name: 'Haplotypes of CHILD on chr1:0–1,000: failed to load; risk state: unavailable' }),
  ).toBeInTheDocument();
});

test('the name says when the phased-marker overlay failed to load (#529, #510)', () => {
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'phased-markers'
      ? { data: undefined, isLoading: false, isError: true }
      : {
          data: { samples: members.map((m) => ({ sample: m.sample_id, segments })) },
          isLoading: false,
          isError: false,
        },
  );
  const { getByRole } = renderTrack(true);

  expect(
    getByRole('img', {
      name: 'Haplotypes of CHILD on chr1:0–1,000; risk state: uninformative; phased markers failed to load',
    }),
  ).toBeInTheDocument();
});

test("marks where a parent's phase was corrected, and says what it means on hover", () => {
  mockData({ segments });
  const { container } = render(
    <HaplotypePhasedTrack
      familyId="F1"
      sampleId="CHILD"
      chrom="1"
      regionStart={0}
      regionEnd={1000}
      width={500}
      height={36}
      role="proband"
      affected={false}
      sex="male"
      highlightRiskHaplotype={false}
      familyMembers={members}
      inheritanceModel="AD"
      riskRegion={null}
      showMarkers={false}
      phaseCorrections={[
        { parent: 'CHILD', side: 'father', chr: '1', position: 400, end: 450, children_switching: 4, children: 5 },
      ]}
    />,
  );
  const canvas = container.querySelector('canvas') as Element;
  // x = 200 over width 500 across region 0–1000 → genomic pos 400, the correction.
  fireEvent.mouseMove(canvas, { clientX: 201, clientY: 10 });
  expect(document.body.querySelector('.viz-tooltip')?.textContent).toContain(
    "Phase corrected: 4 of 5 children switched together on the father's side",
  );
  // Away from it, with markers hidden, nothing.
  fireEvent.mouseMove(canvas, { clientX: 300, clientY: 10 });
  expect(document.body.querySelector('.viz-tooltip')).toBeNull();
});

// CLIN-66: blocks built for another pedigree or other sexes resolve no disease haplotype, as
// the family page calls no embryo from them.
test('reads no risk state from outdated blocks, and says why', () => {
  const trio = [
    { sample_id: 'FATHER', role: 'father', affected: true },
    { sample_id: 'MOTHER', role: 'mother', affected: false },
    { sample_id: 'CHILD', role: 'proband', affected: true },
  ];
  useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) =>
    queryKey[0] === 'haplotypes'
      ? {
          data: {
            chr: '1',
            start: 0,
            end: 1000,
            blocks_outdated: true,
            samples: ['FATHER', 'CHILD'].map((sample) => ({
              sample,
              segments: [{ start: 0, end: 1000, hap1: '0', hap2: '1', ps: 1 }],
            })),
          },
          isLoading: false,
        }
      : { data: undefined, isLoading: false },
  );
  const { container, getByRole } = render(
    <HaplotypePhasedTrack
      familyId="F1"
      sampleId="CHILD"
      chrom="1"
      regionStart={0}
      regionEnd={1000}
      width={500}
      height={36}
      role="proband"
      affected
      sex="male"
      highlightRiskHaplotype
      familyMembers={trio}
      inheritanceModel="AD"
      riskRegion={{ chr: '1', start: 400, end: 600 }}
    />,
  );

  const warning =
    "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.";
  // Before: "affected / at risk", as in the test above.
  expect(
    getByRole('img', {
      name: `Haplotypes of CHILD on chr1:0–1,000; risk state at chr1:400–600: uninformative; ${warning}`,
    }),
  ).toBeInTheDocument();
  expect(container.querySelector('[data-risk-state]')?.getAttribute('data-risk-state')).toBe('uninformative');
  expect(getByRole('note')).toHaveTextContent('Outdated blocks: no risk state');
});

// Found while fixing ENG-19 (#1009): a call written as one allele, a man's one X outside the
// PARs, read '—' (no genotype) in the marker tooltip, as it read '·' in the ROI marker review
// before #1009. The tooltip now writes a call by the review's rule, its two homologs side by side.
describe('the marker tooltip at an X-linked locus', () => {
  const xMembers = [
    { sample_id: 'FATHER', role: 'father', sex: 'male' },
    { sample_id: 'MOTHER', role: 'mother', sex: 'female' },
    { sample_id: 'SON', role: 'embryo', sex: 'male' },
    { sample_id: 'DAUGHTER', role: 'embryo', sex: 'female' },
    { sample_id: 'UNRECORDED', role: 'embryo', sex: null },
  ];
  // Genotypes per site, in member order, over ref A / alt G. The men's calls are written as one
  // allele or twice.
  const xSites = [
    { pos: 100, ref: 'A', alt: 'G', gts: ['0', '0|1', '1', '0|1', '1|1'] },
    { pos: 300, ref: 'A', alt: 'G', gts: ['0|0', '0|1', '1|1', '0|0', '0|1'] },
    // The son's call of two different alleles: a genotype he cannot have on his one X.
    { pos: 500, ref: 'A', alt: 'G', gts: ['1', '0|0', '0|1', '0|0', '0|0'] },
  ];
  const mockX = (ranges: number[][]) =>
    useQueryMock.mockImplementation(({ queryKey }: { queryKey: unknown[] }) => {
      if (queryKey[0] === 'phased-markers') {
        return {
          data: {
            samples: xMembers.map((member) => ({
              sample: member.sample_id,
              markers: xSites.map((site) => ({ pos: site.pos, hap1: null, hap2: 1 })),
              reference: ['FATHER', 'MOTHER'].includes(member.sample_id),
            })),
            sites: xSites,
            hemizygous_x_ranges: ranges,
          },
        };
      }
      if (queryKey[0] === 'haplotypes') {
        return { data: { chr: 'X', start: 0, end: 1000, samples: [] }, isLoading: false };
      }
      return { data: undefined, isLoading: false };
    });
  const renderSonTrack = () =>
    render(
      <HaplotypePhasedTrack
        familyId="F1"
        sampleId="SON"
        chrom="X"
        regionStart={0}
        regionEnd={1000}
        width={500}
        height={36}
        role="embryo"
        affected={false}
        sex="male"
        highlightRiskHaplotype={false}
        familyMembers={xMembers}
        inheritanceModel="XLR"
        riskRegion={null}
        showMarkers
      />,
    );
  const tooltipSpans = () => [...(document.body.querySelector('.viz-tooltip')?.querySelectorAll<HTMLElement>('span') ?? [])];
  // Each member's genotype in the tooltip at a site: the cell after its name and its swatches.
  // x = pos / 2 over width 500 across region 0–1000.
  const genotypesAt = (container: HTMLElement, pos: number): Record<string, string | null | undefined> => {
    fireEvent.mouseMove(container.querySelector('canvas') as Element, { clientX: pos / 2, clientY: 10 });
    const spans = tooltipSpans();
    return Object.fromEntries(
      xMembers.map(({ sample_id }) => [
        sample_id,
        spans.find((span) => span.firstChild?.textContent === sample_id)?.nextElementSibling?.nextElementSibling
          ?.textContent,
      ]),
    );
  };

  test('writes a man’s one X as its one allele, on the side of his X, and no allele on the other', () => {
    mockX([[0, 1000]]);
    const { container } = renderSonTrack();

    // Written once: the father's one X on his first homolog, a son's, his mother's X, on his
    // second, as the track and the ROI marker review draw them.
    expect(genotypesAt(container, 100)).toEqual({
      FATHER: 'A | –',
      MOTHER: 'A | G',
      SON: '– | G',
      DAUGHTER: 'A | G',
      UNRECORDED: 'G | G',
    });
    // Written twice, a man's call is the same one allele, as the QC reads it; a woman, and a
    // member whose sex is not recorded, have two copies.
    expect(genotypesAt(container, 300)).toEqual({
      FATHER: 'A | –',
      MOTHER: 'A | G',
      SON: '– | G',
      DAUGHTER: 'A | A',
      UNRECORDED: 'A | G',
    });
    // A son's call of two different alleles is written as called.
    expect(genotypesAt(container, 500)).toMatchObject({ FATHER: 'G | –', SON: 'A | G' });

    // The side without an allele is muted, as the tooltip's "no genotype" is.
    const probe = document.createElement('span');
    probe.style.color = NUCLEOTIDE_FALLBACK_COLOR;
    const noAllele = tooltipSpans().filter((span) => span.textContent === '–' && span.children.length === 0);
    expect(noAllele.length).toBeGreaterThan(0);
    expect(new Set(noAllele.map((span) => span.style.color))).toEqual(new Set([probe.style.color]));
  });

  test('writes a call of one allele as that allele where a man is not known to have one X', () => {
    mockX([]);
    const { container } = renderSonTrack();

    // The call is what it is; written twice, it is two copies there.
    expect(genotypesAt(container, 100)).toMatchObject({ FATHER: 'A | –', SON: '– | G' });
    expect(genotypesAt(container, 300)).toMatchObject({ FATHER: 'A | A', SON: 'G | G' });
  });
});
