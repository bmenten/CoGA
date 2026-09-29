// Genome-wide repeat-expansion track: the per-sample request, loci placed at chromosome offset + midpoint
// (clamped inside the track), status colours, the locus tooltip, and loading / empty / failed (#510) states.
import { fireEvent, render, screen } from '@testing-library/react';
import type { ComponentProps } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ApiRepeatExpansionTrackItem } from '../../lib/apiTypes';

const { apiGetMock, useQueryMock } = vi.hoisted(() => ({
  apiGetMock: vi.fn(),
  useQueryMock: vi.fn(),
}));

vi.mock('@tanstack/react-query', () => ({ useQuery: useQueryMock }));
vi.mock('../../lib/api', () => ({ default: { get: apiGetMock } }));

import GenomeRepeatExpansionTrack from '../visualizations/GenomeRepeatExpansionTrack';

type Props = ComponentProps<typeof GenomeRepeatExpansionTrack>;
type QueryOptions = {
  queryKey: unknown[];
  queryFn: () => Promise<unknown>;
  enabled?: boolean;
};

const THEME: Record<string, string> = {
  '--color-grid': '#e5e7eb',
  '--color-repeat-normal': '#9ca3af',
  '--color-repeat-review': '#7c3aed',
  '--color-repeat-intermediate': '#d97706',
  '--color-repeat-pathogenic': '#b42318',
  '--color-repeat-unknown': '#c6ccd3',
};

const LAYOUT = {
  offsets: { '1': 0, '4': 1_000_000, X: 2_000_000 },
  lengths: { '1': 1_000_000, '4': 1_000_000, X: 1_000_000 },
  total: 3_000_000,
};

const BASE_PROPS: Props = {
  familyId: 'FAM1',
  sampleId: 'S1',
  chroms: ['1', '4', 'X'],
  layout: LAYOUT,
  width: 300,
  height: 20,
};

const track = (overrides: Partial<Props> = {}) => (
  <GenomeRepeatExpansionTrack {...BASE_PROPS} {...overrides} />
);

const locus = (
  overrides: Partial<ApiRepeatExpansionTrackItem> = {}
): ApiRepeatExpansionTrackItem => ({
  sample: 'S1',
  locus_id: 'HD_HTT',
  gene: 'HTT',
  display_name: 'HTT',
  disease: 'Huntington disease',
  chr: '4',
  start: 490_000,
  end: 510_000,
  motif: 'CAG',
  warning_min: 27,
  pathogenic_min: 40,
  status: 'pathogenic',
  allele_repeat_counts: [17, 45],
  allele_bp_lengths: [51, 135],
  ...overrides,
});

const serve = (state: {
  data?: unknown;
  isLoading?: boolean;
  isError?: boolean;
  refetch?: () => void;
}) =>
  useQueryMock.mockReturnValue({
    data: undefined,
    isLoading: false,
    isError: false,
    refetch: vi.fn(),
    ...state,
  });

const lastQueryOptions = (): QueryOptions => {
  const call = useQueryMock.mock.calls.at(-1);
  if (!call) throw new Error('useQuery was not called');
  return call[0] as QueryOptions;
};

const markers = (container: HTMLElement) =>
  Array.from(container.querySelectorAll('svg > rect')).map((rect) => ({
    x: Math.round(Number(rect.getAttribute('x')) * 1e6) / 1e6,
    fill: rect.getAttribute('fill'),
  }));

describe('GenomeRepeatExpansionTrack', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    useQueryMock.mockReset();
    Object.entries(THEME).forEach(([name, value]) =>
      document.documentElement.style.setProperty(name, value)
    );
  });

  afterEach(() => {
    Object.keys(THEME).forEach((name) =>
      document.documentElement.style.removeProperty(name)
    );
  });

  it('requests the sample’s loci for the displayed chromosomes and project, on encoded path segments', async () => {
    serve({});
    apiGetMock.mockResolvedValue({ data: { items: [] } });
    render(track({ familyId: 'FAM 1', sampleId: 'S/1', projectId: 'P1' }));

    const options = lastQueryOptions();
    expect(options.queryKey).toEqual([
      'genome-repeat-expansions',
      'FAM 1',
      'S/1',
      '1,4,X',
      'P1',
    ]);
    expect(options.enabled).toBe(true);
    await expect(options.queryFn()).resolves.toEqual({ items: [] });
    // Identifiers are single encoded segments (#521); every chromosome is its own chr= param.
    expect(apiGetMock).toHaveBeenCalledWith(
      '/families/FAM%201/repeat-expansions/sample/S%2F1?chr=1&chr=4&chr=X&project_id=P1'
    );
  });

  it('leaves project_id off the request when no project is selected', async () => {
    serve({});
    apiGetMock.mockResolvedValue({ data: { items: [] } });
    render(track());

    await lastQueryOptions().queryFn();
    expect(apiGetMock).toHaveBeenCalledWith(
      '/families/FAM1/repeat-expansions/sample/S1?chr=1&chr=4&chr=X'
    );
  });

  it('does not request anything while no chromosome is displayed', () => {
    serve({});
    render(track({ chroms: [] }));

    expect(lastQueryOptions().enabled).toBe(false);
  });

  it('places each locus at its chromosome offset plus locus midpoint, matching chr-prefixed names', () => {
    serve({
      data: {
        items: [
          locus({
            locus_id: 'NIID_NOTCH2NLC',
            display_name: 'NOTCH2NLC',
            chr: '1',
            start: 390_000,
            end: 410_000,
            status: 'normal',
          }),
          locus({ chr: 'chr4' }), // HTT, midpoint 500 kb → genome 1.5 Mb
          locus({
            locus_id: 'FXS_FMR1',
            display_name: 'FMR1',
            chr: 'X',
            start: 245_000,
            end: 255_000,
          }),
        ],
      },
    });
    // 3 Mb on 300 px: 10 kb per px; each marker is 4 px wide and centred on its locus.
    const { container } = render(track());

    expect(markers(container).map((marker) => marker.x)).toEqual([
      38, 148, 223,
    ]);
    container.querySelectorAll('svg > rect').forEach((rect) => {
      expect(rect).toHaveAttribute('width', '4');
      expect(rect).toHaveAttribute('y', '5');
      expect(rect).toHaveAttribute('height', '10');
    });
    const baseline = container.querySelector('svg > line');
    expect(baseline).toHaveAttribute('y1', '10');
    expect(baseline).toHaveAttribute('x2', '300');
    expect(baseline).toHaveAttribute('stroke', '#e5e7eb');
  });

  it('keeps markers at the very ends of the genome fully inside the track', () => {
    serve({
      data: {
        items: [
          locus({ locus_id: 'first', chr: '1', start: 0, end: 10 }),
          locus({ locus_id: 'last', chr: 'X', start: 999_990, end: 1_000_000 }),
        ],
      },
    });
    const { container } = render(track());

    // Centres clamp to [3, width − 3], so the 4-px markers span 1–5 px and 295–299 px.
    expect(markers(container).map((marker) => marker.x)).toEqual([1, 295]);
  });

  it('does not draw loci on chromosomes outside the layout', () => {
    serve({
      data: {
        items: [
          locus({
            locus_id: 'DM1_DMPK',
            display_name: 'DMPK',
            chr: '19',
            start: 45_770_204,
            end: 45_770_266,
          }),
          locus(),
        ],
      },
    });
    const { container } = render(track());

    expect(markers(container).map((marker) => marker.x)).toEqual([148]);
  });

  it('colours each locus by its repeat status; an unrecognised status reads as unknown', () => {
    const statuses = [
      'normal',
      'review',
      'intermediate',
      'pathogenic',
      'unknown',
      'expanded',
    ];
    serve({
      data: {
        items: statuses.map((status, index) =>
          locus({
            locus_id: `L${index}`,
            chr: '1',
            start: index * 100_000,
            end: index * 100_000 + 60,
            status: status as ApiRepeatExpansionTrackItem['status'],
          })
        ),
      },
    });
    const { container } = render(track());

    // In position order (the markers are drawn by severity, see below).
    expect(
      [...markers(container)].sort((a, b) => a.x - b.x).map((marker) => marker.fill),
    ).toEqual([
      '#9ca3af', // normal
      '#7c3aed', // review
      '#d97706', // intermediate
      '#b42318', // pathogenic
      '#c6ccd3', // unknown
      '#c6ccd3', // 'expanded' is not a status the track knows: never painted as a called status
    ]);
  });

  it('draws the most severe status on top, so a pathogenic locus is not covered (#526)', () => {
    // FMR1 (pathogenic) and AFF2 (normal) are 588 kb apart on chrX: at genome scale their
    // 4 px markers overlap, and in API order (by position) AFF2 was drawn over FMR1.
    serve({
      data: {
        items: [
          locus({ locus_id: 'FMR1', chr: 'X', start: 147_912_049, end: 147_912_110, status: 'pathogenic' }),
          locus({ locus_id: 'AFF2', chr: 'X', start: 148_500_604, end: 148_500_700, status: 'normal' }),
          locus({ locus_id: 'OTHER', chr: '1', start: 100, end: 160, status: 'intermediate' }),
        ],
      },
    });
    const { container } = render(track());
    // SVG paints in document order: the last marker is on top.
    expect(markers(container).map((marker) => marker.fill)).toEqual([
      '#9ca3af', // normal AFF2
      '#d97706', // intermediate
      '#b42318', // pathogenic FMR1, last
    ]);
  });

  it('shows the locus, disease and allele repeat counts on hover, and clears on leave', () => {
    serve({
      data: {
        items: [
          locus(),
          locus({
            locus_id: 'FXS_FMR1',
            display_name: 'FMR1',
            disease: 'Fragile X syndrome',
            chr: 'X',
            allele_repeat_counts: [],
          }),
        ],
      },
    });
    const { container } = render(track());
    const [htt, fmr1] = Array.from(container.querySelectorAll('svg > rect'));
    const tooltipLines = () =>
      Array.from(
        document.body.querySelector('.viz-tooltip')?.children ?? []
      ).map((line) => line.textContent);

    fireEvent.mouseMove(htt, { clientX: 150, clientY: 10 });
    expect(tooltipLines()).toEqual([
      'HTT',
      'Huntington disease',
      '17 / 45 repeats',
    ]);

    // Without allele calls the tooltip says so rather than showing an empty count.
    fireEvent.mouseMove(fmr1, { clientX: 225, clientY: 10 });
    expect(tooltipLines().slice(0, 2)).toEqual(['FMR1', 'Fragile X syndrome']);
    expect(tooltipLines()[2]).toContain('no call');

    fireEvent.mouseLeave(fmr1);
    expect(document.body.querySelector('.viz-tooltip')).toBeNull();
  });

  it('shows a loading overlay, not the empty message, while the loci load', () => {
    serve({ isLoading: true });
    render(track());

    expect(screen.getByText('Loading repeat expansions')).toBeInTheDocument();
    expect(
      screen.queryByText('No repeat loci in view')
    ).not.toBeInTheDocument();
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('a failed request shows the failure with retry, never "no repeat loci" (#510)', () => {
    const refetch = vi.fn();
    serve({ isError: true, refetch });
    render(track());

    expect(screen.getByRole('alert')).toHaveTextContent(
      'Could not load repeat expansions — this is not an empty result.'
    );
    expect(
      screen.queryByText('No repeat loci in view')
    ).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(refetch).toHaveBeenCalledTimes(1);
  });

  it('a sample without repeat calls reads as empty', () => {
    serve({ data: { items: [] } });
    const { container } = render(track());

    expect(
      screen.getByText('No repeat loci in view')
    ).toBeInTheDocument();
    expect(markers(container)).toHaveLength(0);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('draws no markers before the genome layout is known (no positions to place them at)', () => {
    serve({ data: { items: [locus()] } });
    const { container } = render(track({ layout: null }));

    expect(markers(container)).toHaveLength(0);
  });

  it('resolves the status palette once per mount, not once per locus or per hover', () => {
    const styleReads = vi.spyOn(window, 'getComputedStyle');
    try {
      serve({ data: { items: [locus()] } });
      const single = render(track());
      const readsForOneLocus = styleReads.mock.calls.length;
      single.unmount();
      expect(readsForOneLocus).toBeGreaterThan(0);

      styleReads.mockClear();
      serve({
        data: {
          items: Array.from({ length: 40 }, (_, index) =>
            locus({
              locus_id: `L${index}`,
              chr: '1',
              start: index * 20_000,
              end: index * 20_000 + 60,
            })
          ),
        },
      });
      const { container } = render(track());
      expect(markers(container)).toHaveLength(40);
      expect(styleReads.mock.calls.length).toBe(readsForOneLocus);

      fireEvent.mouseMove(container.querySelector('svg > rect') as Element, {
        clientX: 5,
        clientY: 5,
      });
      expect(document.body.querySelector('.viz-tooltip')).not.toBeNull();
      expect(styleReads.mock.calls.length).toBe(readsForOneLocus);
    } finally {
      styleReads.mockRestore();
    }
  });

  describe('accessible name (#529)', () => {
    it('names the drawn loci, the pathogenic ones by name', () => {
      serve({
        data: {
          items: [
            locus(), // HTT, pathogenic
            locus({ locus_id: 'FXS_FMR1', display_name: 'FMR1', chr: 'X', status: 'intermediate' }),
            locus({ locus_id: 'NIID_NOTCH2NLC', display_name: 'NOTCH2NLC', chr: '1', status: 'normal' }),
            // Off the displayed chromosomes: not drawn, so not counted.
            locus({ locus_id: 'DM1_DMPK', display_name: 'DMPK', chr: '19' }),
          ],
        },
      });
      render(track());

      expect(
        screen.getByRole('img', {
          name: 'Repeat loci of S1 in view: 3, 1 pathogenic (HTT), 1 intermediate',
        }),
      ).toBeInTheDocument();
    });

    it('names three pathogenic loci and counts the rest, and counts review and unknown', () => {
      serve({
        data: {
          items: [
            ...['P1', 'P2', 'P3', 'P4', 'P5'].map((name, index) =>
              locus({ locus_id: name, display_name: name, chr: '1', start: index * 1000 })
            ),
            locus({ locus_id: 'R1', chr: '4', status: 'review' }),
            locus({ locus_id: 'U1', chr: 'X', status: 'unknown' }),
            // A status the track does not know is drawn, and counted, as unknown.
            locus({
              locus_id: 'E1',
              chr: 'X',
              start: 600_000,
              status: 'expanded' as ApiRepeatExpansionTrackItem['status'],
            }),
          ],
        },
      });
      render(track());

      expect(
        screen.getByRole('img', {
          name: 'Repeat loci of S1 in view: 8, 5 pathogenic (P1, P2, P3 +2 more), 1 needing review, 2 unknown',
        }),
      ).toBeInTheDocument();
    });

    it('says when every drawn locus is normal', () => {
      serve({
        data: {
          items: [
            locus({ status: 'normal' }),
            locus({ locus_id: 'FXS_FMR1', display_name: 'FMR1', chr: 'X', status: 'normal' }),
          ],
        },
      });
      render(track());

      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: 2, all normal' })
      ).toBeInTheDocument();
    });

    it('an empty view is named as empty', () => {
      serve({ data: { items: [] } });
      render(track());

      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: none' })
      ).toBeInTheDocument();
    });

    it('a load is named as loading, and a failure as a failure — never as zero loci (#510)', () => {
      serve({ isLoading: true });
      const { unmount } = render(track());
      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: loading' })
      ).toBeInTheDocument();
      unmount();

      serve({ isError: true });
      render(track());
      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: failed to load' })
      ).toBeInTheDocument();
    });

    // #602 — a track that asked for nothing found nothing: it is not "none".
    it('before the genome layout is known it is loading, not empty', () => {
      serve({ data: { items: [locus()] } });
      render(track({ layout: null }));

      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: loading' })
      ).toBeInTheDocument();
      expect(screen.queryByText('No repeat loci in view')).not.toBeInTheDocument();
    });

    it('with no chromosome in view it asks for nothing and says so', () => {
      serve({});
      render(track({ chroms: [] }));

      expect(lastQueryOptions().enabled).toBe(false);
      expect(
        screen.getByRole('img', { name: 'Repeat loci of S1 in view: no chromosomes in view' })
      ).toBeInTheDocument();
      expect(screen.queryByText('No repeat loci in view')).not.toBeInTheDocument();
    });
  });
});
