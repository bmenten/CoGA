import { fireEvent, render } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({ useQueryMock: vi.fn() }));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock, keepPreviousData: (previous: unknown) => previous }));
vi.mock('react-router-dom', () => ({ useNavigate: () => vi.fn() }));
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
