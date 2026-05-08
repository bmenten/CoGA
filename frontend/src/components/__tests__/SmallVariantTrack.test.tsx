import { render, screen } from '@testing-library/react';
import { beforeEach, expect, test, vi } from 'vitest';

const { apiGetMock, useQueryMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({
  useQuery: useQueryMock,
}));

vi.mock('../../lib/api', () => ({
  default: {
    get: apiGetMock,
  },
}));
import SmallVariantTrack from '../visualizations/SmallVariantTrack';

beforeEach(() => {
  apiGetMock.mockReset();
  useQueryMock.mockReset();
});

test('renders message when no small variants', () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );
  expect(
    screen.getByText(/no small variants for this region \/ sample/i)
  ).toBeInTheDocument();
});

test('renders loader while small variants are loading', () => {
  useQueryMock.mockReturnValue({ data: undefined, isLoading: true });
  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );
  expect(screen.getByText(/loading small variants/i)).toBeInTheDocument();
});

test('requests small variants carried by the displayed sample before pagination', async () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  apiGetMock.mockResolvedValue({ data: { variants: [] } });

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
    />
  );

  await useQueryMock.mock.calls[0][0].queryFn();

  expect(apiGetMock).toHaveBeenCalledWith(
    '/families/F1/small-variants',
    expect.objectContaining({
      params: expect.objectContaining({
        sample_filter: 'S1:0/1|1/0|0|1|1|0|1/1|1|1',
      }),
    }),
  );
});

test('preserves an explicit small-variant sample filter', async () => {
  useQueryMock.mockReturnValue({ data: { variants: [] }, isLoading: false });
  apiGetMock.mockResolvedValue({ data: { variants: [] } });

  render(
    <SmallVariantTrack
      familyId="F1"
      sampleId="S1"
      chrom="1"
      regionStart={0}
      regionEnd={100}
      width={100}
      height={20}
      filters={{ sample_filter: 'S1:1/1', source: 'glimpse2' }}
    />
  );

  await useQueryMock.mock.calls[0][0].queryFn();

  expect(apiGetMock).toHaveBeenCalledWith(
    '/families/F1/small-variants',
    expect.objectContaining({
      params: expect.objectContaining({
        sample_filter: 'S1:1/1',
        source: 'glimpse2',
      }),
    }),
  );
});
