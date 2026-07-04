import { fireEvent, render } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock, keepPreviousData: (previous: unknown) => previous }));
vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import HaplotypePhasedTrack from '../visualizations/HaplotypePhasedTrack';

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

const mockData = (opts: { segments?: typeof segments; truncated?: boolean }) => {
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
          sites: [
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
  expect(tooltip?.textContent).toContain('1:100');
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
  expect(tooltip?.textContent).toContain('1:100');
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
