import { render, screen } from '@testing-library/react';
import { vi } from 'vitest';

const { useQueryMock } = vi.hoisted(() => ({
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
}));

import VariantTrack from '../visualizations/VariantTrack';

test('renders structural variant types on separate vertical rows', () => {
  useQueryMock.mockReturnValue({
    data: {
      variants: [
        {
          chr: '1',
          start: 10,
          end: 30,
          type: 'DEL',
          genotypes: [{ sample: 'S1', gt: '0/1' }],
        },
        {
          chr: '1',
          start: 12,
          end: 32,
          type: 'DUP',
          genotypes: [{ sample: 'S1', gt: '1/1' }],
        },
      ],
    },
    isLoading: false,
  });

  const { container } = render(
    <VariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={80}
    />
  );

  expect(screen.getByText('DEL')).toBeInTheDocument();
  expect(screen.getByText('DUP')).toBeInTheDocument();
  expect(screen.getByText('INV')).toBeInTheDocument();
  expect(screen.getByText('INS')).toBeInTheDocument();
  expect(screen.getByText('BND')).toBeInTheDocument();

  const delVariant = container.querySelector('[data-variant-type="DEL"]');
  const dupVariant = container.querySelector('[data-variant-type="DUP"]');

  expect(delVariant).not.toBeNull();
  expect(dupVariant).not.toBeNull();
  expect(Number(delVariant?.getAttribute('y'))).toBeLessThan(Number(dupVariant?.getAttribute('y')));
});

test('renders message when no structural variants are available', () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });

  render(
    <VariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={80}
    />
  );

  expect(screen.getByText(/no svs for this region \/ sample/i)).toBeInTheDocument();
});
