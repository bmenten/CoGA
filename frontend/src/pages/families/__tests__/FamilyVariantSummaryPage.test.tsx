// Pins the family variant summary's shell and its structural-variant section: a tab per kind of
// data the family has, the open one's section shown and the others hidden; the SV requests
// (encoded id, 100k cap), per-chromosome/type and per-sample counts, the unique/shared sample
// matrix, absolute-length histograms, states, and that nothing shows before the family and its
// reference.
// The small-variant, repeat, Paraphase and mtDNA sections: VariantSummarySections.test.tsx.
import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import FamilyVariantSummaryPage from '../FamilyVariantSummaryPage';
import api from '../../../lib/api';
import { renderFamilyRoute } from '../../../test/familyRoute';
import {
  INCOMPLETE_IMPORT_METADATA,
  findAssemblyScopeBanner,
  findImportIncompleteBanner,
  offScopeProject,
} from '../../../test/familyPageBanners';
import { held, settle } from '../../../test/settle';
import {
  SUMMARY_FAMILY as FAMILY,
  failWith,
  serveVariantSummary,
  sv,
  type SummaryAnswers,
} from '../../../test/variantSummaryRoutes';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn() } }));

// Drawing the bars is Histogram's concern (it has its own test); what the page decides is
// which lengths each histogram is fed, on which size bins and scale.
vi.mock('../../../components/visualizations/Histogram', () => ({
  default: ({
    data,
    binLabels,
    logScale,
  }: {
    data: number[];
    binLabels?: string[];
    logScale?: boolean;
  }) => (
    <div
      data-testid="histogram"
      data-bins={binLabels?.join('|')}
      data-log-scale={String(logScale)}
    >
      {data.join(' ')}
    </div>
  ),
}));

const mockedGet = vi.mocked(api.get);

const serve = (familyId: string, answers: SummaryAnswers = {}) =>
  serveVariantSummary(mockedGet, familyId, answers);

const renderPage = (path = '/families/F1/variant-summary') =>
  renderFamilyRoute(
    '/families/:familyId/variant-summary',
    path,
    <FamilyVariantSummaryPage />
  );

const sectionTitled = (title: string) => {
  const section = screen
    .getByRole('heading', { name: title, level: 2 })
    .closest('section');
  expect(section).not.toBeNull();
  return section as HTMLElement;
};

const readTable = (table: HTMLElement) =>
  within(table)
    .getAllByRole('row')
    .map((row) =>
      Array.from(row.querySelectorAll('th, td')).map((cell) => cell.textContent)
    );

const svTable = (name: string) => screen.getByRole('table', { name });

/** The histogram under a sub-heading (a variant type or a caller) of the SV section. */
const histogramUnder = (heading: string, group: 'type' | 'caller') => {
  const panel = within(sectionTitled('Structural variants'))
    .getAllByRole('heading', { name: heading, level: 4 })
    .map((h) => h.parentElement as HTMLElement)
    .find((p) => p.dataset.histogramGroup === group);
  expect(panel).toBeDefined();
  return within(panel as HTMLElement).getByTestId('histogram');
};

describe('FamilyVariantSummaryPage', () => {
  beforeEach(() => {
    mockedGet.mockReset();
  });

  it("asks for the family's SV lengths (capped at 100k), sharing and per-sample counts, with the id encoded", async () => {
    // A family id with a path separator must stay one segment, not reach another endpoint.
    serve('FAM%201%2F2', {
      lengths: [sv('1', 'DEL')],
      shared: { S1: { S1: 1 } },
    });
    renderPage('/families/FAM%201%2F2/variant-summary');

    expect(await screen.findByText(/Total variants: 1\b/)).toBeInTheDocument();
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/FAM%201%2F2/structural-variant-lengths',
      {
        params: { limit: 100000 },
      }
    );
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/FAM%201%2F2/shared-structural-variant-counts'
    );
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/FAM%201%2F2/structural-variant-sample-counts'
    );
    expect(mockedGet).toHaveBeenCalledWith('/families/FAM%201%2F2');
  });

  it('opens with the shared family header, pedigree included', async () => {
    serve('F1', { lengths: [sv('1', 'DEL')], shared: { S1: { S1: 1 } } });
    renderPage();

    expect(
      await screen.findByRole('link', { name: 'Family F1' })
    ).toHaveAttribute('href', '/families/F1');
    expect(
      screen.getByText('Variant summary', { selector: '.page-kicker' })
    ).toBeInTheDocument();
    expect(await screen.findByText('Pedigree')).toBeInTheDocument();
    // The histograms' scale sits above them, not in the header.
    const scale = await screen.findByRole('button', { name: 'Linear scale' });
    expect(scale.closest('.page-top-card')).toBeNull();
    expect(scale.closest('.analysis-graph-toolbar')).toHaveTextContent(
      'Length histograms'
    );
  });

  it('opens with a tab per kind the family has, the first one open and the others hidden', async () => {
    serve('F1', {
      has: {
        small: true,
        structural: true,
        repeats: false,
        paraphase: false,
        mito: true,
      },
      lengths: [sv('1', 'DEL')],
    });
    renderPage();

    const tabs = await screen.findByRole('tablist', {
      name: 'Variant data of this family',
    });
    expect(
      within(tabs)
        .getAllByRole('tab')
        .map((tab) => [tab.textContent, tab.getAttribute('aria-selected')])
    ).toEqual([
      ['Small variants (SNV/indel)', 'true'],
      ['Structural variants', 'false'],
      ['Mitochondrial DNA', 'false'],
    ]);
    // Only the open tab's section is shown, labelled by its tab.
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    ).toEqual(['Small variants (SNV/indel)']);
    expect(screen.getByRole('tabpanel')).toHaveAccessibleName(
      'Small variants (SNV/indel)'
    );
    // A tab not opened is not loaded, and a kind the family lacks is not asked for.
    await settle(mockedGet);
    const asked = () => mockedGet.mock.calls.map(([url]) => url as string);
    expect(asked()).not.toContain('/families/F1/structural-variant-lengths');
    expect(asked()).not.toContain('/families/F1/mitochondrial-dna');
    expect(asked()).not.toContain('/families/F1/repeat-expansions');
    expect(asked()).not.toContain('/families/F1/paraphase');
  });

  it('opens a tab on a click, hiding the one before, which keeps what it loaded', async () => {
    serve('F1', {
      has: { small: true, structural: true },
      lengths: [sv('1', 'DEL')],
    });
    renderPage();

    const smallTab = await screen.findByRole('tab', {
      name: 'Small variants (SNV/indel)',
    });
    fireEvent.click(screen.getByRole('tab', { name: 'Structural variants' }));

    expect(await screen.findByText(/Total variants: 1\b/)).toBeInTheDocument();
    expect(
      screen.getByRole('tab', { name: 'Structural variants' })
    ).toHaveAttribute('aria-selected', 'true');
    expect(smallTab).toHaveAttribute('aria-selected', 'false');
    expect(
      screen.getAllByRole('heading', { level: 2 }).map((h) => h.textContent)
    ).toEqual(['Structural variants']);
    // The small-variant section is hidden, not dropped: back on its tab it is not asked again.
    await settle(mockedGet);
    const statistics = () =>
      mockedGet.mock.calls.filter(
        ([url]) => url === '/families/F1/small-variant-statistics'
      ).length;
    expect(statistics()).toBe(1);
    fireEvent.click(smallTab);
    expect(
      screen.getByRole('heading', {
        name: 'Small variants (SNV/indel)',
        level: 2,
      })
    ).toBeInTheDocument();
    // The SV section is hidden in its turn.
    expect(screen.getByText(/Total variants: 1\b/)).not.toBeVisible();
    await settle(mockedGet);
    expect(statistics()).toBe(1);
  });

  it('opens the tab the link names, and moves between tabs with the arrow keys', async () => {
    serve('F1', {
      has: { small: true, structural: true, mito: true },
      lengths: [sv('1', 'DEL')],
    });
    renderPage('/families/F1/variant-summary#mitochondrial-dna');

    const mito = await screen.findByRole('tab', { name: 'Mitochondrial DNA' });
    expect(mito).toHaveAttribute('aria-selected', 'true');
    expect(mito).toHaveAttribute('tabindex', '0');
    expect(
      screen.getByRole('tab', { name: 'Small variants (SNV/indel)' })
    ).toHaveAttribute('tabindex', '-1');
    expect(
      screen.getByRole('heading', { name: 'Mitochondrial DNA', level: 2 })
    ).toBeInTheDocument();

    // The arrows move the focus, round the row; Enter or Space (a click) opens the tab.
    fireEvent.keyDown(mito, { key: 'ArrowRight' });
    expect(
      screen.getByRole('tab', { name: 'Small variants (SNV/indel)' })
    ).toHaveFocus();
    fireEvent.keyDown(document.activeElement as HTMLElement, {
      key: 'ArrowLeft',
    });
    expect(mito).toHaveFocus();
    fireEvent.keyDown(mito, { key: 'Home' });
    expect(
      screen.getByRole('tab', { name: 'Small variants (SNV/indel)' })
    ).toHaveFocus();
    expect(mito).toHaveAttribute('aria-selected', 'true');
  });

  it('opens the first tab when the link names a kind the family lacks', async () => {
    serve('F1', { has: { small: true, structural: false } });
    renderPage('/families/F1/variant-summary#structural-variants');

    expect(
      await screen.findByRole('tab', { name: 'Small variants (SNV/indel)' })
    ).toHaveAttribute('aria-selected', 'true');
    expect(
      screen.queryByRole('tab', { name: 'Structural variants' })
    ).not.toBeInTheDocument();
  });

  it('says so when the family has no variant data', async () => {
    serve('F1', { has: { structural: false } });
    renderPage();

    expect(
      await screen.findByText('This family has no variant data to summarise.')
    ).toBeInTheDocument();
    expect(screen.queryByRole('tablist')).not.toBeInTheDocument();
  });

  it('gives a kind whose check failed a tab, so its section can say for itself', async () => {
    serve('F1', {
      has: { structural: false, paraphase: failWith(new Error('down')) },
    });
    renderPage();

    expect(
      await screen.findByRole('tab', { name: 'Paraphase' })
    ).toHaveAttribute('aria-selected', 'true');
    expect(
      await screen.findByRole('heading', { name: 'Paraphase', level: 2 })
    ).toBeInTheDocument();
    expect(
      screen.queryByRole('tab', { name: 'Structural variants' })
    ).not.toBeInTheDocument();
  });

  // Like every family page, its header warns of a partly imported family and of one off the
  // validated scope.
  it('warns in its header that the import is incomplete and the assembly is not validated', async () => {
    serve('F1', {
      family: {
        ...FAMILY,
        projects: ['p1'],
        metadata: INCOMPLETE_IMPORT_METADATA,
      },
      projects: [offScopeProject('p1')],
      lengths: [sv('1', 'DEL')],
      shared: { S1: { S1: 1 } },
    });
    renderPage();

    const header = (
      await screen.findByRole('heading', { name: 'Family F1' })
    ).closest('.page-top-card');
    expect(header).toContainElement(await findImportIncompleteBanner());
    expect(header).toContainElement(await findAssemblyScopeBanner());
  });

  it('asks the presence checks and the small-variant statistics for the project of the link', async () => {
    serve('F1', {
      family: { ...FAMILY, projects: ['p0', 'p1'] },
      projects: [offScopeProject('p0'), offScopeProject('p1')],
      has: { small: true, structural: false },
    });
    renderPage('/families/F1/variant-summary?project_id=p1');

    await screen.findByRole('heading', {
      name: 'Small variants (SNV/indel)',
      level: 2,
    });
    await settle(mockedGet);
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/F1/small-variants?count_only=true&project_id=p1'
    );
    expect(mockedGet).toHaveBeenCalledWith(
      '/families/F1/small-variant-statistics',
      {
        params: { project_id: 'p1' },
      }
    );
  });

  it('shows the SV section loading until its three requests are in', async () => {
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      shared: new Promise<never>(() => undefined),
    });
    renderPage();

    expect(
      await screen.findByText('Loading the structural-variant summary…')
    ).toBeInTheDocument();
    // Half a summary is not shown: the counts wait for the matrix.
    expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
    expect(
      within(sectionTitled('Structural variants')).queryByRole('table')
    ).not.toBeInTheDocument();
  });

  it('counts SVs per chromosome and type, in karyotype order, with a total per chromosome', async () => {
    serve('F1', {
      lengths: [
        sv('2', 'DEL'),
        sv('10', 'DUP'),
        sv('X', 'DEL'),
        sv('1', 'DEL'),
        sv('1', 'INS'),
        sv('2', 'DEL'),
        sv('MT', 'DEL'),
        sv('Y', 'INV'),
        // A call without a chromosome or type is counted, not dropped.
        sv('', ''),
      ],
      shared: { S1: { S1: 9 } },
    });
    renderPage();

    expect(await screen.findByText(/Total variants: 9\b/)).toBeInTheDocument();
    expect(
      readTable(svTable('Structural variants per chromosome and type'))
    ).toEqual([
      ['Chromosome', 'DEL', 'DUP', 'INS', 'INV', 'unknown', 'Total'],
      ['1', '1', '0', '1', '0', '0', '2'],
      ['2', '2', '0', '0', '0', '0', '2'],
      ['10', '0', '1', '0', '0', '0', '1'],
      ['X', '1', '0', '0', '0', '0', '1'],
      ['Y', '0', '0', '0', '1', '0', '1'],
      ['MT', '1', '0', '0', '0', '0', '1'],
      ['unknown', '0', '0', '0', '0', '1', '1'],
    ]);
    // Below the cap nothing is said about truncation.
    expect(screen.queryByText(/showing first/)).not.toBeInTheDocument();
  });

  it('counts each member’s SVs by type, a member without SV calls included', async () => {
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      svSamples: [
        { sample_id: 'S1', total: 3, by_type: { DEL: 2, DUP: 1 } },
        { sample_id: 'S2', total: 1, by_type: { INV: 1 } },
        { sample_id: 'S3', total: 0, by_type: {} },
      ],
    });
    renderPage();

    await screen.findByText(/Total variants/);
    expect(
      readTable(svTable('Structural variants per sample and type'))
    ).toEqual([
      ['Sample', 'DEL', 'DUP', 'INV', 'Total'],
      ['S1', '2', '1', '0', '3'],
      ['S2', '0', '0', '1', '1'],
      ['S3', '0', '0', '0', '0'],
    ]);
  });

  it('says when the 100,000-SV cap was reached, so the counts are known to be partial', async () => {
    serve('F1', {
      lengths: Array.from({ length: 100000 }, () => sv('1', 'DEL', 50)),
      shared: { S1: { S1: 100000 } },
    });
    renderPage();

    expect(
      await screen.findByText(/Total variants: 100000 \(showing first 100000\)/)
    ).toBeInTheDocument();
  });

  it('tabulates SVs unique to each sample on the diagonal and pairwise-shared ones off it', async () => {
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      // Keys arrive in any order; a pair the server left out reads 0, not blank; a member
      // without SV calls (S4) keeps a row and column of zeros rather than dropping out.
      shared: {
        S3: { S1: 3, S2: 1, S3: 6, S4: 0 },
        S1: { S1: 5, S2: 4, S3: 3, S4: 0 },
        S4: { S1: 0, S2: 0, S3: 0, S4: 0 },
        S2: { S1: 4, S2: 7, S4: 0 },
      },
    });
    renderPage();

    await screen.findByText(/Total variants/);
    expect(readTable(svTable('Shared and unique structural variants'))).toEqual(
      [
        ['Sample', 'S1', 'S2', 'S3', 'S4'],
        ['S1', '5', '4', '3', '0'],
        ['S2', '4', '7', '0', '0'],
        ['S3', '3', '1', '6', '0'],
        ['S4', '0', '0', '0', '0'],
      ]
    );
    expect(
      screen.getByText(
        /Diagonal counts denote variants unique to the individual; off-diagonal/
      )
    ).toBeInTheDocument();
  });

  it('shows no totals for the sharing matrix, whose cells do not add up to one', async () => {
    // One SV carried by all three of a trio is in every pair's cell: the row sums read
    // 2 each and the grand sum 6, while the family has one SV.
    serve('F1', {
      lengths: [sv('1', 'DEL')],
      shared: {
        M: { M: 0, F: 1, P: 1 },
        F: { M: 1, F: 0, P: 1 },
        P: { M: 1, F: 1, P: 0 },
      },
    });
    renderPage();

    await screen.findByText(/Total variants: 1\b/);
    const rows = readTable(svTable('Shared and unique structural variants'));
    expect(rows[0]).toEqual(['Sample', 'F', 'M', 'P']);
    expect(rows.map((row) => row[0])).not.toContain('Total');
    expect(
      screen.getByText(
        /counted in every pair, so the cells do not add up to a total/
      )
    ).toBeInTheDocument();
  });

  it('bins every SV by its absolute length — overall, per type and per caller', async () => {
    serve('F1', {
      lengths: [
        // Deletions carry a negative SVLEN; they are binned by size all the same.
        sv('1', 'DEL', -1500, 'sniffles'),
        sv('2', 'DUP', 3000, 'cnvkit'),
        sv('3', 'DEL', -250, null),
        sv('4', 'INV', 20000, 'sniffles'),
      ],
      shared: { S1: { S1: 4 } },
    });
    renderPage();

    await screen.findByText(/Total variants/);
    const all = within(
      within(sectionTitled('Structural variants')).getByRole('heading', {
        name: 'All lengths',
      }).parentElement as HTMLElement
    ).getByTestId('histogram');
    expect(all).toHaveTextContent('1500 3000 250 20000');
    expect(all).toHaveAttribute(
      'data-bins',
      '0|1-10|10-100|100-1k|1k-10k|10k-100k|100k-1M|>1M'
    );

    expect(histogramUnder('DEL', 'type')).toHaveTextContent(/^1500 250$/);
    expect(histogramUnder('DUP', 'type')).toHaveTextContent(/^3000$/);
    expect(histogramUnder('INV', 'type')).toHaveTextContent(/^20000$/);

    // A call without a recorded caller is grouped as "unknown", not lost.
    expect(histogramUnder('sniffles', 'caller')).toHaveTextContent(
      /^1500 20000$/
    );
    expect(histogramUnder('cnvkit', 'caller')).toHaveTextContent(/^3000$/);
    expect(histogramUnder('unknown', 'caller')).toHaveTextContent(/^250$/);
  });

  it('switches every length histogram between log and linear scale', async () => {
    serve('F1', {
      lengths: [sv('1', 'DEL'), sv('2', 'DUP')],
      shared: { S1: { S1: 2 } },
    });
    renderPage();

    await screen.findByText(/Total variants/);
    const section = sectionTitled('Structural variants');
    const scales = () =>
      within(section)
        .getAllByTestId('histogram')
        .map((h) => h.getAttribute('data-log-scale'));
    // All + two types + one caller.
    expect(scales()).toEqual(['true', 'true', 'true', 'true']);

    fireEvent.click(
      within(section).getByRole('button', { name: 'Linear scale' })
    );
    expect(scales()).toEqual(['false', 'false', 'false', 'false']);

    fireEvent.click(within(section).getByRole('button', { name: 'Log scale' }));
    expect(scales()).toEqual(['true', 'true', 'true', 'true']);
  });

  it('shows a family whose SV check failed and that has none as zero counts rather than an error', async () => {
    serve('F1', {
      has: { structural: failWith(new Error('down')) },
      lengths: [],
      shared: {},
    });
    renderPage();

    expect(await screen.findByText(/Total variants: 0\b/)).toBeInTheDocument();
    expect(
      readTable(svTable('Structural variants per chromosome and type'))
    ).toEqual([['Chromosome', 'Total']]);
    expect(
      within(
        within(sectionTitled('Structural variants')).getByRole('heading', {
          name: 'All lengths',
        }).parentElement as HTMLElement
      ).getByTestId('histogram')
    ).toBeEmptyDOMElement();
  });

  it('does not present a failed SV load as a zero-count summary, and keeps the other sections', async () => {
    serve('F1', {
      has: { small: true },
      lengths: failWith({
        response: { status: 500, data: { detail: 'ClickHouse unavailable' } },
      }),
      shared: { S1: { S1: 1 } },
    });
    renderPage('/families/F1/variant-summary#structural-variants');

    const section = await waitFor(() => {
      const found = sectionTitled('Structural variants');
      expect(within(found).getByRole('alert')).toBeInTheDocument();
      return found;
    });
    expect(within(section).getByRole('alert')).toHaveTextContent(
      'Could not load the structural-variant summary — this is not an empty result. ClickHouse unavailable'
    );
    expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
    expect(within(section).queryByRole('table')).not.toBeInTheDocument();
    expect(
      within(section).getByRole('button', { name: 'Retry' })
    ).toBeInTheDocument();
    // The small-variant section is not held back by it.
    fireEvent.click(
      screen.getByRole('tab', { name: 'Small variants (SNV/indel)' })
    );
    expect(
      screen.getByRole('heading', {
        name: 'Small variants (SNV/indel)',
        level: 2,
      })
    ).toBeInTheDocument();
  });

  // The family record holds what the header warns of: an import that left the
  // family incomplete, and an assembly outside the validated scope. The summary is not shown
  // without those warnings, neither while the record loads nor when it could not be loaded.
  describe('without the family record', () => {
    it('shows none of the summary before the family, and then its reference, are in', async () => {
      const family = held();
      const reference = held();
      serve('F1', {
        // The family on project p1, whose assembly is outside the validated scope.
        family: family.answered.then(() => ({
          data: { ...FAMILY, projects: ['p1'] },
        })),
        projects: reference.answered.then(() => ({
          data: [offScopeProject('p1')],
        })),
        lengths: [sv('1', 'DEL')],
        shared: { S1: { S1: 1 } },
      });
      renderPage();

      await settle(mockedGet);
      expect(
        screen.getByRole('status', { name: 'Loading variant summary' })
      ).toBeInTheDocument();
      expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
      // No section's data is asked for before then.
      expect(mockedGet).not.toHaveBeenCalledWith(
        '/families/F1/structural-variant-lengths',
        expect.anything()
      );

      // The family alone is not enough: without its reference, the scope warning is unknown.
      family.release();
      await settle(mockedGet);
      expect(
        screen.getByRole('status', { name: 'Loading variant summary' })
      ).toBeInTheDocument();
      expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();

      reference.release();
      expect(
        await screen.findByText(/Total variants: 1\b/)
      ).toBeInTheDocument();
      expect(
        screen.getByRole('heading', { name: 'Family F1' })
      ).toBeInTheDocument();
      await findAssemblyScopeBanner();
    });

    it('says the family could not be loaded, shows none of the summary, and loads it on retry', async () => {
      serve('F1', {
        family: failWith({
          response: { status: 500, data: { detail: 'Postgres unavailable' } },
        }),
        lengths: [sv('1', 'DEL')],
        shared: { S1: { S1: 1 } },
      });
      renderPage();

      const failure = (
        await screen.findByRole('heading', {
          name: 'Family could not be loaded',
        })
      ).closest('.page-state') as HTMLElement;
      await settle(mockedGet);
      expect(failure).toBeInTheDocument();
      expect(failure).toHaveTextContent(
        'Postgres unavailable This is a failed request, not a missing family.'
      );
      expect(screen.queryByText(/Total variants/)).not.toBeInTheDocument();
      expect(screen.queryByRole('table')).not.toBeInTheDocument();

      serve('F1', { lengths: [sv('1', 'DEL')], shared: { S1: { S1: 1 } } });
      fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
      expect(
        await screen.findByText(/Total variants: 1\b/)
      ).toBeInTheDocument();
      expect(
        screen.getByRole('heading', { name: 'Family F1' })
      ).toBeInTheDocument();
    });
  });
});
