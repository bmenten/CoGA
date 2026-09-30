import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';
import CircosPlotPage from '../CircosPlotPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const { circosSpy, mockedChroms } = vi.hoisted(() => ({
  circosSpy: vi.fn(),
  mockedChroms: [...Array.from({ length: 22 }, (_, index) => String(index + 1)), 'X', 'Y'],
}));

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    defaults: {
      baseURL: 'http://test-api',
    },
  },
}));

vi.mock('../../../components/visualizations/CircosPlot', () => ({
  default: (props: any) => {
    circosSpy(props);
    const firstChrom = props.chromData[0];
    return (
      <div data-testid="circos-plot">
        {firstChrom?.chr}:{firstChrom?.bands?.length ?? 0}
      </div>
    );
  },
  CHROMS: mockedChroms,
}));

type Handler = () => Promise<unknown>;

const ok = (data: unknown): Handler => () => Promise.resolve({ data });
const failure: Handler = () =>
  Promise.reject(Object.assign(new Error('HTTP 500'), { response: { status: 500 } }));
/** Fails the first time, then answers. */
const failingOnce = (data: unknown): Handler => {
  let calls = 0;
  return () => (++calls === 1 ? failure() : Promise.resolve({ data }));
};

/** The rows /chromosomes/{assembly}/details serves, named as given, one band each. */
const chromosomeRows = (names: string[], sizeOf: (name: string) => number = () => 1_000_000) =>
  names.map((name) => ({
    _id: `id-${name}`,
    assembly_id: 'asm-1',
    chr: name,
    size: sizeOf(name),
    bands: [{ name: `${name}p1`, start: 0, end: sizeOf(name), stain: 'gneg' }],
  }));

const HUMAN = [...mockedChroms];
const GRCH38_ROWS = chromosomeRows(HUMAN);

const projectOn = (assemblyName: string) => ({
  id: 'p1',
  name: 'Project 1',
  assembly_id: 'asm-1',
  assembly_name: assemblyName,
  assembly_version: '',
  families: [],
  samples: [],
});

/**
 * Family F1, linked to project p1 on the given assembly. Its SVs are asked for with the
 * project's scope. A URL not listed is answered with an error, so a request the page
 * should not make (another assembly's chromosomes, say) cannot pass unnoticed.
 */
const serve = (
  assemblyName: string,
  routes: Record<string, Handler> = {},
) => {
  const table: Record<string, Handler> = {
    '/families/F1': ok({ projects: ['p1'] }),
    '/projects': ok([projectOn(assemblyName)]),
    [`/chromosomes/${encodeURIComponent(assemblyName)}/details`]: ok(GRCH38_ROWS),
    '/families/F1/structural-variants?project_id=p1&page_size=0': ok({ variants: [] }),
    ...routes,
  };
  (api.get as unknown as Mock).mockImplementation((url: string) =>
    (table[url] ?? (() => Promise.reject(new Error(`Unexpected GET ${url}`))))(),
  );
};

const requestedUrls = () => (api.get as unknown as Mock).mock.calls.map(([url]) => String(url));

const renderPage = (path = '/families/F1/circos') =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/families/:familyId/circos" element={<CircosPlotPage />} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );

const drawnChromosomes = () => circosSpy.mock.calls.at(-1)?.[0].chromData;

describe('CircosPlotPage', () => {
  beforeEach(() => {
    (api.get as unknown as Mock).mockReset();
    circosSpy.mockClear();
  });

  it("draws the chromosomes of the family's assembly, at that assembly's sizes, with their bands", async () => {
    const t2tSize = (name: string) => (name === '1' ? 248387328 : 1_000_000);
    serve('T2T-CHM13v2.0', {
      '/chromosomes/T2T-CHM13v2.0/details': ok(chromosomeRows(HUMAN, t2tSize)),
    });
    renderPage();

    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
    expect(drawnChromosomes()[0]).toEqual({
      chr: '1',
      size: 248387328,
      bands: [{ name: '1p1', start: 0, end: 248387328, stain: 'gneg' }],
    });
    expect(drawnChromosomes().map((chrom: { chr: string }) => chrom.chr)).toEqual(HUMAN);
    expect(screen.getByText(/across the chromosomes of T2T-CHM13v2\.0/)).toBeInTheDocument();
    // Never GRCh38's sizes for a family on another assembly.
    expect(requestedUrls()).toContain('/chromosomes/T2T-CHM13v2.0/details');
    expect(requestedUrls().some((url) => url.includes('GRCh38'))).toBe(false);
  });

  it("asks for the assembly's chromosomes with its name as one path segment", async () => {
    serve('Build 2/custom', {
      '/chromosomes/Build%202%2Fcustom/details': ok(GRCH38_ROWS),
    });
    renderPage();

    await screen.findByTestId('circos-plot');
    expect(requestedUrls()).toContain('/chromosomes/Build%202%2Fcustom/details');
  });

  it('draws only the chromosomes the assembly has, in karyotype order, and lists only those', async () => {
    // Shuffled, spelled with chr, without chrY, and with the contigs and the mitochondrion
    // a cytoband file also holds.
    const withoutY = HUMAN.filter((name) => name !== 'Y');
    serve('GRCh38', {
      '/chromosomes/GRCh38/details': ok(
        chromosomeRows([
          'chrM',
          'chr1_KI270706v1_random',
          'chrUn_GL000195v1',
          'chrEBV',
          ...[...withoutY].reverse().map((name) => `chr${name}`),
        ]),
      ),
    });
    renderPage();

    await screen.findByTestId('circos-plot');
    expect(drawnChromosomes().map((chrom: { chr: string }) => chrom.chr)).toEqual(withoutY);
    const listed = screen.getAllByRole('checkbox').map((box) => box.closest('label')?.textContent);
    expect(listed).toEqual(withoutY.map((name) => `chr${name}`));
  });

  it('preserves project scope in the structural-variant circos query', async () => {
    serve('GRCh38');
    renderPage('/families/F1/circos?project_id=p1');

    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
    expect(requestedUrls()).toContain('/families/F1/structural-variants?project_id=p1&page_size=0');
  });

  describe('fails visibly instead of drawing GRCh38', () => {
    it('for a family without a linked project', async () => {
      serve('GRCh38', { '/families/F1': ok({ projects: [] }) });
      renderPage();

      expect(await screen.findByText('Reference not linked')).toBeInTheDocument();
      expect(screen.queryByTestId('circos-plot')).not.toBeInTheDocument();
      // Neither chromosomes nor SVs are asked for without the family's assembly.
      expect(requestedUrls().some((url) => url.startsWith('/chromosomes/'))).toBe(false);
      expect(requestedUrls().some((url) => url.includes('/structural-variants'))).toBe(false);
    });

    it('when the family could not be loaded, and retries', async () => {
      serve('GRCh38', { '/families/F1': failingOnce({ projects: ['p1'] }) });
      renderPage();

      expect(await screen.findByText('Family could not be loaded')).toBeInTheDocument();
      expect(requestedUrls().some((url) => url.startsWith('/chromosomes/'))).toBe(false);

      screen.getByRole('button', { name: 'Retry' }).click();
      await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
    });

    it("when the family's project, and so its assembly, could not be loaded, and retries", async () => {
      serve('GRCh38', { '/projects': failingOnce([projectOn('GRCh38')]) });
      renderPage();

      expect(await screen.findByText('Reference could not be loaded')).toBeInTheDocument();
      expect(requestedUrls().some((url) => url.startsWith('/chromosomes/'))).toBe(false);

      screen.getByRole('button', { name: 'Retry' }).click();
      await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
    });

    it('for an assembly with chromosomes the plot cannot place, naming them', async () => {
      const horse = [...Array.from({ length: 31 }, (_, index) => String(index + 1)), 'X', 'M'];
      serve('EquCab3.0', { '/chromosomes/EquCab3.0/details': ok(chromosomeRows(horse)) });
      renderPage();

      expect(await screen.findByText('The circos plot cannot draw EquCab3.0')).toBeInTheDocument();
      expect(
        screen.getByText(
          /It draws chromosomes 1–22, X and Y\. EquCab3\.0 also has chr23, chr24, chr25, chr26, chr27, chr28, chr29, chr30, chr31, which it cannot place/,
        ),
      ).toBeInTheDocument();
      expect(screen.queryByTestId('circos-plot')).not.toBeInTheDocument();
    });

    it("for an assembly with none of the plot's chromosomes", async () => {
      serve('GCF_000001405.40', {
        '/chromosomes/GCF_000001405.40/details': ok(chromosomeRows(['NC_000001.11', 'NC_000002.12'])),
      });
      renderPage();

      expect(await screen.findByText('The circos plot cannot draw GCF_000001405.40')).toBeInTheDocument();
      expect(
        screen.getByText('It draws chromosomes 1–22, X and Y, and GCF_000001405.40 has none of them.'),
      ).toBeInTheDocument();
      expect(screen.queryByTestId('circos-plot')).not.toBeInTheDocument();
    });

    it('for an assembly without chromosome sizes', async () => {
      serve('GRCh38', { '/chromosomes/GRCh38/details': ok([]) });
      renderPage();

      expect(await screen.findByText('No chromosome sizes for GRCh38')).toBeInTheDocument();
      expect(screen.queryByTestId('circos-plot')).not.toBeInTheDocument();
    });
  });

  // #589: a failure is shown as a failure, never as a plot still loading or without SVs;
  // a capped SV list is not drawn as the whole.
  it('says the chromosomes failed to load and offers a retry, instead of loading forever', async () => {
    serve('GRCh38', { '/chromosomes/GRCh38/details': failingOnce(GRCH38_ROWS) });
    renderPage();

    expect(await screen.findByText('Could not load the chromosomes')).toBeInTheDocument();
    expect(screen.getByText(/The circos plot needs the chromosome sizes of GRCh38\./)).toBeInTheDocument();
    expect(screen.queryByText('Loading circos plot')).not.toBeInTheDocument();

    screen.getByRole('button', { name: 'Retry' }).click();
    await waitFor(() => expect(screen.getByTestId('circos-plot')).toHaveTextContent('1:1'));
  });

  it('says the SVs failed to load, not that the family has none', async () => {
    serve('GRCh38', { '/families/F1/structural-variants?project_id=p1&page_size=0': failure });
    renderPage();

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load structural variants — this is not an empty result.',
    );
    expect(screen.queryByText('No variants for this family.')).not.toBeInTheDocument();
    expect(circosSpy.mock.calls.at(-1)?.[0].variants).toBeUndefined();
  });

  it('does not draw a capped SV list as the whole genome', async () => {
    serve('GRCh38', {
      '/families/F1/structural-variants?project_id=p1&page_size=0': ok({
        total: 50000,
        total_is_estimated: true,
        count_limit: 50000,
        variants: [{ chr: '1', start: 10, end: 40, type: 'DEL' }],
      }),
    });
    renderPage();

    expect(await screen.findByRole('status')).toHaveTextContent(
      'Too many structural variants to draw (more than 50,000).',
    );
    const props = circosSpy.mock.calls.at(-1)?.[0];
    expect(props.variants).toBeUndefined();
    expect(props.tooManyVariants).toBe(true);
  });
});
