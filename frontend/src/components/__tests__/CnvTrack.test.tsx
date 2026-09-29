import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock, keepPreviousData: (previous: unknown) => previous }));
vi.mock('react-router', () => ({ useNavigate: () => vi.fn() }));
vi.mock('../../lib/api', () => ({ default: { get: vi.fn() } }));

import CnvTrack from '../visualizations/CnvTrack';

beforeEach(() => {
  useQueryMock.mockReset();
});

test('shows a hover tooltip with the CNV name', () => {
  useQueryMock.mockReturnValue({
    data: [
      { start: 100, end: 200, type: 'DEL', label: '1q21.1 recurrent microdeletion' },
    ],
  });

  const { container } = render(
    <CnvTrack
      assembly="GRCh38"
      chrom="1"
      width={100}
      height={20}
      regionStart={0}
      regionEnd={300}
    />,
  );

  const rect = container.querySelector('rect[aria-label]');
  expect(rect).not.toBeNull();
  fireEvent.mouseMove(rect as Element, { clientX: 30, clientY: 10 });

  const tooltip = document.body.querySelector('.viz-tooltip');
  expect(tooltip).not.toBeNull();
  expect(tooltip).toHaveClass('viz-tooltip--floating');
  expect(tooltip?.textContent).toContain('1q21.1 recurrent microdeletion');
});

test('does not draw a held clinical CNV outside the new window while a pan loads (#526)', () => {
  const region = (regionStart: number, regionEnd: number) => (
    <CnvTrack
      assembly="GRCh38"
      chrom="22"
      width={400}
      height={20}
      regionStart={regionStart}
      regionEnd={regionEnd}
    />
  );
  useQueryMock.mockReturnValue({
    data: [
      { start: 18_900_000, end: 19_000_000, type: 'DEL', label: '22q11.2 proximal (A-B)' },
      { start: 20_300_000, end: 20_700_000, type: 'DEL', label: '22q11.2 distal' },
    ],
  });
  const { container, rerender } = render(region(18_000_000, 22_000_000));
  expect(container.querySelectorAll('rect[aria-label]')).toHaveLength(2);

  // Same span, 1.5 Mb right: the first region is now entirely left of the window. While
  // the new window loads, it used to be drawn at the left edge under its own name.
  useQueryMock.mockReturnValue({ data: undefined });
  rerender(region(19_500_000, 23_500_000));
  const shown = Array.from(container.querySelectorAll('rect[aria-label]')).map((rect) =>
    rect.getAttribute('aria-label'),
  );
  expect(shown).toEqual([expect.stringContaining('22q11.2 distal')]);
  // The track's own name counts only what is in view (#529).
  expect(
    screen.getByRole('img', { name: 'Clinical CNVs on chr22:19,500,000–23,500,000: 1 (22q11.2 distal)' }),
  ).toBeInTheDocument();

  // Further right, no held region lies in view and the window has not arrived: not "none".
  rerender(region(21_000_000, 25_000_000));
  expect(
    screen.getByRole('img', { name: 'Clinical CNVs on chr22:21,000,000–25,000,000: loading' }),
  ).toBeInTheDocument();
});

const renderRegion = (chrom: string, regionStart: number, regionEnd: number) =>
  render(
    <CnvTrack
      assembly="GRCh38"
      chrom={chrom}
      width={400}
      height={20}
      regionStart={regionStart}
      regionEnd={regionEnd}
    />,
  );

test('names the clinical CNVs it draws for a screen reader, and keeps each one\'s name (#529)', () => {
  useQueryMock.mockReturnValue({
    data: [
      { _id: 'a', start: 18_900_000, end: 19_000_000, type: 'DEL', label: '22q11.2 proximal (A-B)' },
      { _id: 'b', start: 20_300_000, end: 20_700_000, type: 'DEL', label: '22q11.2 distal' },
    ],
  });

  const { container } = renderRegion('22', 18_000_000, 22_000_000);

  expect(
    screen.getByRole('img', {
      name: 'Clinical CNVs on chr22:18,000,000–22,000,000: 2 (22q11.2 proximal (A-B), 22q11.2 distal)',
    }),
  ).toBeInTheDocument();
  expect(container.querySelectorAll('rect[aria-label]')).toHaveLength(2);
});

test('names at most three, as many as fit in about 150 characters, then "…" (#529)', () => {
  // Overlapping 1q21.1 entries, named as in the ClinGen reference file.
  useQueryMock.mockReturnValue({
    data: [
      { _id: 'a', start: 145_000_000, end: 146_000_000, label: '1q21.1 recurrent (TAR syndrome) region (proximal, BP2-BP3) (includes RBM8A)' },
      { _id: 'b', start: 146_000_000, end: 147_000_000, label: '1q21.1 recurrent region (distal, BP3-BP4) (includes GJA5)' },
      { _id: 'c', start: 145_000_000, end: 147_000_000, label: '1q21.1 recurrent (TAR syndrome) region (proximal and distal, BP1-BP4) (includes RBM8A and GJA5)' },
      { _id: 'd', start: 148_000_000, end: 149_000_000, label: '1q21.2' },
    ],
  });
  const { unmount } = renderRegion('1', 144_000_000, 150_000_000);
  expect(
    screen.getByRole('img', {
      name: 'Clinical CNVs on chr1:144,000,000–150,000,000: 4 (1q21.1 recurrent (TAR syndrome) region (proximal, BP2-BP3) (includes RBM8A), …)',
    }),
  ).toBeInTheDocument();
  unmount();

  // A single name too long to fit is cut short.
  useQueryMock.mockReturnValue({ data: [{ _id: 'e', start: 145_000_000, end: 146_000_000, label: 'x'.repeat(200) }] });
  renderRegion('1', 144_000_000, 150_000_000);
  const name = screen.getByRole('img').getAttribute('aria-label') ?? '';
  expect(name.length).toBeLessThanOrEqual(150);
  expect(name).toMatch(/^Clinical CNVs on chr1:144,000,000–150,000,000: 1 \(x+…\)$/);
});

test.each([
  ['an empty region', { data: [] }, 'none'],
  ['a request in flight', { data: undefined }, 'loading'],
  ['a failed request (#510)', { data: undefined, isError: true, refetch: vi.fn() }, 'failed to load'],
])('names %s as what it is, and only an empty region as "none" (#529)', (_label, state, summary) => {
  useQueryMock.mockReturnValue(state);

  renderRegion('22', 18_000_000, 22_000_000);

  expect(
    screen.getByRole('img', { name: `Clinical CNVs on chr22:18,000,000–22,000,000: ${summary}` }),
  ).toBeInTheDocument();
});
