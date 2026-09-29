import { fireEvent, render } from '@testing-library/react';
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
});
