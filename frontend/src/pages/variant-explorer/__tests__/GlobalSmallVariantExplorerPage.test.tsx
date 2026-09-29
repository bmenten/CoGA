// The cross-project variant explorer page: every search scoped to the chosen assembly, keyset
// paging and sorting, the sample genotype and annotation filters, the carrier dialog and CSV export.

import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { createTestQueryClient } from '../../../test/createTestQueryClient';
import type { SmallVariantTagDefinition } from '../../families/smallVariantSearch';
import GlobalSmallVariantExplorerPage from '../GlobalSmallVariantExplorerPage';
import type {
  GlobalVariantPage,
  GlobalVariantRow,
  VariantCarriers,
  VariantExplorerAssembly,
} from '../types';

const { apiGetMock } = vi.hoisted(() => ({ apiGetMock: vi.fn() }));

vi.mock('../../../lib/api', () => ({
  default: { get: apiGetMock },
}));

const ASSEMBLIES: VariantExplorerAssembly[] = [
  { assembly_id: 'asm-38', assembly_name: 'GRCh38', version: 'p14', project_count: 3 },
  { assembly_id: 'asm-37', assembly_name: 'GRCh37', version: null, project_count: 1 },
];

const TAGS: SmallVariantTagDefinition[] = [
  {
    key: 'review',
    label: 'Review',
    group: 'collaboration',
    color: '#2563eb',
    sort_order: 10,
    scope: 'system',
    is_custom: false,
  },
];

const PANELS = [{ _id: 'panel-1', name: 'Hereditary cancer' }];

const makeVariant = (overrides: Partial<GlobalVariantRow>): GlobalVariantRow => ({
  key: '101',
  variant_id: '13-32316461-G-A',
  chr: '13',
  pos: 32316461,
  ref: 'G',
  alt: 'A',
  type: 'SNV',
  gene: 'BRCA2',
  gene_symbols: ['BRCA2'],
  impact: 'MODERATE',
  consequence: 'missense_variant',
  effects: ['missense_variant'],
  hgvsc: 'c.7007G>A',
  clinvar: 'Pathogenic',
  classification: null,
  tags: [],
  total_samples: 1,
  het_samples: 1,
  hom_samples: 0,
  total_families: 1,
  ...overrides,
});

// Distinct counts, so each count button in the row has its own name.
const BRCA2 = makeVariant({
  key: '101',
  tags: ['review'],
  total_samples: 5,
  het_samples: 3,
  hom_samples: 2,
  total_families: 4,
});
const MECP2 = makeVariant({
  key: '102',
  variant_id: 'X-154030912-C-CT',
  chr: 'X',
  pos: 154030912,
  ref: 'C',
  alt: 'CT',
  type: 'INDEL',
  gene: 'MECP2',
  gene_symbols: ['MECP2'],
  hgvsc: null,
});
const TP53 = makeVariant({
  key: '103',
  variant_id: '17-7674220-C-T',
  chr: '17',
  pos: 7674220,
  ref: 'C',
  alt: 'T',
  gene: 'TP53',
  gene_symbols: ['TP53'],
});

const page = (overrides: Partial<GlobalVariantPage> = {}): GlobalVariantPage => ({
  total: 2,
  total_is_estimated: false,
  page: 1,
  page_size: 50,
  next_cursor: null,
  assembly_id: 'asm-38',
  assembly_name: 'GRCh38',
  variants: [BRCA2, MECP2],
  ...overrides,
});

const CARRIERS_URL = '/variant-explorer/small-variants/101/carriers';

const CARRIERS: VariantCarriers = {
  key: '101',
  total_families: 1,
  total_samples: 1,
  het_samples: 1,
  hom_samples: 0,
  families: [
    {
      family_id: 'F1',
      family_uuid: 'uuid-f1',
      project_name: 'Rare disease',
      carrier_count: 1,
      samples: [
        {
          sample_id: 'S1',
          role: 'proband',
          genotype: '0/1',
          zygosity: 'heterozygous',
          family_id: 'F1',
          family_uuid: 'uuid-f1',
        },
      ],
    },
  ],
};

type Reply = Promise<{ data: unknown }>;

const reply = (data: unknown): Reply => Promise.resolve({ data });

const deferred = () => {
  let resolve: (value: { data: unknown }) => void = () => {};
  let reject: (reason: unknown) => void = () => {};
  const promise = new Promise<{ data: unknown }>((onResolve, onReject) => {
    resolve = onResolve;
    reject = onReject;
  });
  return { promise, resolve, reject };
};

const mockApi = ({
  assemblies = () => reply(ASSEMBLIES),
  variants = () => reply(page()),
  exportCsv = () => reply('Chromosome,Position\n13,32316461\n'),
}: {
  assemblies?: () => Reply;
  variants?: (params: URLSearchParams) => Reply;
  exportCsv?: () => Reply;
} = {}) => {
  apiGetMock.mockImplementation((url: string) => {
    const [path, query = ''] = url.split('?');
    switch (path) {
      case '/variant-explorer/assemblies':
        return assemblies();
      case '/variant-explorer/small-variant-tags':
        return reply(TAGS);
      case '/panels':
        return reply(PANELS);
      case '/variant-explorer/samples':
        return reply(['S1', 'S2']);
      case '/variant-explorer/small-variants':
        return variants(new URLSearchParams(query));
      case '/variant-explorer/small-variants/export':
        return exportCsv();
      case CARRIERS_URL:
        return reply(CARRIERS);
      default:
        return Promise.reject(new Error(`Unexpected GET ${url}`));
    }
  });
};

// The URLs of the result-table searches, oldest first.
const variantUrls = (): string[] =>
  apiGetMock.mock.calls
    .map(([url]) => String(url))
    .filter((url) => url.startsWith('/variant-explorer/small-variants?'));

const lastVariantRequest = (): URLSearchParams => {
  const urls = variantUrls();
  return new URLSearchParams(urls[urls.length - 1]?.split('?')[1] ?? '');
};

const renderPage = () => {
  const queryClient = createTestQueryClient();
  render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={['/variant-explorer']}>
        <GlobalSmallVariantExplorerPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { queryClient };
};

const findResults = () => screen.findByRole('link', { name: 'BRCA2' });

const rowOf = (gene: string) =>
  screen.getByRole('link', { name: gene }).closest('tr') as HTMLElement;

// The sample genotype filter's card (the annotation filter form has Clear buttons of its own).
const genotypeCard = () =>
  within(screen.getByText('Sample - Genotype Filter').closest('section') as HTMLElement);

describe('GlobalSmallVariantExplorerPage', () => {
  beforeEach(() => {
    apiGetMock.mockReset();
    mockApi();
  });

  describe('what the explorer can apply (#526)', () => {
    it('offers no filter its backend drops, and never sends one', async () => {
      renderPage();
      await findResults();
      // The shared form offered these; the explorer's endpoint read none of them, so an
      // applied chip claimed a filter the results did not reflect.
      expect(screen.queryByPlaceholderText('Transcript')).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/^Intervals:/)).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/^Excluded genes:/)).not.toBeInTheDocument();
      expect(screen.queryByPlaceholderText(/^Excluded intervals:/)).not.toBeInTheDocument();
      expect(screen.queryByText('Excluded standard tags')).not.toBeInTheDocument();
      expect(screen.queryByText('Only show variants with saved notes')).not.toBeInTheDocument();
      const quickExclude = screen.getByLabelText('Quick exclude') as HTMLSelectElement;
      expect([...quickExclude.options].map((option) => option.value)).toEqual([
        'all',
        'benign_likely_benign',
        'custom',
      ]);
      // What the explorer applies is still there: one locus, genes, ClinVar, review tags.
      expect(screen.getByPlaceholderText(/^Gene list:/)).toBeInTheDocument();
      expect(screen.getByText('Excluded ClinVar status')).toBeInTheDocument();

      const request = lastVariantRequest();
      for (const unread of ['intervals', 'transcript', 'exclude_gene', 'exclude_intervals', 'exclude_review_tag', 'has_notes', 'locus', 'inheritance', 'prioritize']) {
        expect(request.has(unread), unread).toBe(false);
      }
      // The ClinVar P/LP rescue, on by default, is now applied by the explorer too.
      expect(request.get('clinvar_overrides_frequency')).toBe('true');
    });

    it('shows a capped total as N+, not as an exact count', async () => {
      mockApi({ variants: () => reply(page({ total: 10000, total_is_estimated: true })) });
      renderPage();
      await findResults();
      expect(screen.getByText(/^10,000\+ variants/)).toBeInTheDocument();
    });

    it('does not read a loading assembly list as an empty result', async () => {
      const assemblies = deferred();
      mockApi({ assemblies: () => assemblies.promise });
      renderPage();
      expect(await screen.findByText('Loading the accessible assemblies…')).toBeInTheDocument();
      expect(screen.queryByText(/No variants match/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^0 variants/)).not.toBeInTheDocument();
      assemblies.resolve({ data: ASSEMBLIES });
      await findResults();
    });

    it('shows a failed assembly list as a failure with a retry, never as no variants', async () => {
      let fail = true;
      mockApi({
        assemblies: () => (fail ? Promise.reject(new Error('500')) : reply(ASSEMBLIES)),
      });
      renderPage();
      expect(await screen.findByRole('alert')).toHaveTextContent(
        'Could not load the accessible assemblies — this is not an empty result.',
      );
      expect(screen.queryByText(/No variants match/)).not.toBeInTheDocument();
      expect(screen.queryByText(/^0 variants/)).not.toBeInTheDocument();
      fail = false;
      fireEvent.click(screen.getByRole('button', { name: 'Retry' }));
      await findResults();
    });

    it('says so when the user can reach no assembly at all', async () => {
      mockApi({ assemblies: () => reply([]) });
      renderPage();
      expect(
        await screen.findByText(/You have no access to a project with small variants/),
      ).toBeInTheDocument();
      expect(screen.queryByText(/No variants match/)).not.toBeInTheDocument();
    });
  });

  describe('assembly scope', () => {
    it('defaults to the most common accessible assembly and scopes every search to it', async () => {
      renderPage();
      await findResults();

      const assembly = screen.getByLabelText('Assembly') as HTMLSelectElement;
      expect(assembly.value).toBe('asm-38');
      expect([...assembly.options].map((option) => option.textContent)).toEqual([
        'GRCh38 (p14) · 3 projects',
        'GRCh37 · 1 project',
      ]);
      // No search goes out before an assembly is chosen, so none goes out without one.
      expect(variantUrls().length).toBeGreaterThan(0);
      for (const url of variantUrls()) {
        expect(new URLSearchParams(url.split('?')[1]).get('assembly_id')).toBe('asm-38');
      }
      const params = lastVariantRequest();
      expect(params.get('sort')).toBe('total_samples');
      expect(params.get('order')).toBe('desc');
      expect(params.get('page_size')).toBe('50');
      expect(params.has('cursor')).toBe(false);
      // Imputed calls stay out unless asked for; no sample constraint until one is applied.
      expect(params.has('include_imputed')).toBe(false);
      expect(params.has('sample_gt')).toBe(false);

      expect(screen.getByText('2 variants')).toBeInTheDocument();
      expect(rowOf('MECP2')).toHaveTextContent('X:154030912 C>CT');
      // Tags are labelled from the explorer's own tag definitions.
      expect(within(rowOf('BRCA2')).getByText('Review')).toBeInTheDocument();
    });

    it('suggests the samples of the selected assembly for the genotype filter', async () => {
      renderPage();
      await findResults();

      expect(apiGetMock).toHaveBeenCalledWith('/variant-explorer/samples', {
        params: { assembly_id: 'asm-38' },
      });
      const listId = genotypeCard().getByPlaceholderText('add sample…').getAttribute('list') ?? '';
      await waitFor(() =>
        expect(
          [...(document.getElementById(listId)?.querySelectorAll('option') ?? [])].map((option) =>
            option.getAttribute('value'),
          ),
        ).toEqual(['S1', 'S2']),
      );
    });

    it('searches the other assembly, and suggests its samples, once it is chosen', async () => {
      const user = userEvent.setup();
      renderPage();
      await findResults();

      await user.selectOptions(screen.getByLabelText('Assembly'), 'asm-37');

      await waitFor(() => expect(lastVariantRequest().get('assembly_id')).toBe('asm-37'));
      expect(apiGetMock).toHaveBeenCalledWith('/variant-explorer/samples', {
        params: { assembly_id: 'asm-37' },
      });
    });

    it('sends no search when no assembly is accessible', async () => {
      mockApi({ assemblies: () => reply([]) });
      const { queryClient } = renderPage();

      await waitFor(() =>
        expect(apiGetMock).toHaveBeenCalledWith('/variant-explorer/assemblies'),
      );
      await waitFor(() => expect(queryClient.isFetching()).toBe(0));
      expect(screen.getByRole('option', { name: 'No accessible assemblies' })).toBeInTheDocument();
      expect(variantUrls()).toEqual([]);
      expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled();
    });
  });

  describe('result states', () => {
    it('shows that variants are loading, not that none match', async () => {
      mockApi({ variants: () => new Promise(() => {}) });
      renderPage();

      expect(await screen.findByText('Loading variants…')).toBeInTheDocument();
      expect(screen.queryByText(/No variants match/)).not.toBeInTheDocument();
      expect(screen.queryByRole('link', { name: 'BRCA2' })).not.toBeInTheDocument();
    });

    it('reports a failed search as a failure, never as "no variants match"', async () => {
      let attempts = 0;
      mockApi({
        variants: () => {
          attempts += 1;
          return attempts === 1
            ? Promise.reject(new Error('Request failed with status code 500'))
            : reply(page({ total: 0, variants: [] }));
        },
      });
      renderPage();

      // With the server's reason and a retry (#606).
      const failure = await screen.findByRole('alert');
      expect(failure).toHaveTextContent(
        'Could not load the variants — this is not an empty result. Request failed with status code 500',
      );
      expect(screen.queryByText(/No variants match/)).not.toBeInTheDocument();
      expect(screen.queryByText('Loading variants…')).not.toBeInTheDocument();
      // Nor does the header count a failed search as none.
      expect(screen.queryByText('0 variants')).not.toBeInTheDocument();

      fireEvent.click(within(failure).getByRole('button', { name: 'Retry' }));
      expect(await screen.findByText('0 variants')).toBeInTheDocument();
    });

    it('says no variants match an empty search, and offers neither export nor paging', async () => {
      mockApi({ variants: () => reply(page({ total: 0, variants: [] })) });
      renderPage();

      await waitFor(() => {
        expect(variantUrls()).toHaveLength(1);
        expect(screen.queryByText('Loading variants…')).not.toBeInTheDocument();
      });
      expect(
        screen.getByText('No variants match the current filters in your accessible projects.'),
      ).toBeInTheDocument();
      expect(screen.getByText('0 variants')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled();
      expect(screen.queryByRole('button', { name: 'Next' })).not.toBeInTheDocument();
    });

    it.each([
      [1234, `${(1234).toLocaleString()} variants`],
      [1, '1 variant'],
    ])('counts a total of %s matching variants as "%s"', async (total, heading) => {
      mockApi({ variants: () => reply(page({ total, variants: [BRCA2], next_cursor: null })) });
      renderPage();
      await findResults();

      expect(screen.getByText(heading)).toBeInTheDocument();
    });
  });

  describe('paging and sorting', () => {
    it('pages forward with the server cursor and back again', async () => {
      const user = userEvent.setup();
      mockApi({
        variants: (params) =>
          reply(
            params.get('cursor') === 'CURSOR-2'
              ? page({ total: 3, variants: [TP53], next_cursor: null })
              : page({ total: 3, next_cursor: 'CURSOR-2' }),
          ),
      });
      renderPage();
      await findResults();

      expect(screen.getByText('Page 1')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();

      await user.click(screen.getByRole('button', { name: 'Next' }));
      expect(await screen.findByRole('link', { name: 'TP53' })).toBeInTheDocument();
      expect(screen.getByText('Page 2')).toBeInTheDocument();
      expect(lastVariantRequest().get('cursor')).toBe('CURSOR-2');
      // The last page offers no next page.
      expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

      await user.click(screen.getByRole('button', { name: 'Previous' }));
      expect(await screen.findByRole('link', { name: 'BRCA2' })).toBeInTheDocument();
      expect(screen.getByText('Page 1')).toBeInTheDocument();
      await waitFor(() => expect(lastVariantRequest().has('cursor')).toBe(false));
    });

    it('keeps the current page on screen while the next loads, without paging from it', async () => {
      const user = userEvent.setup();
      const nextPage = deferred();
      mockApi({
        variants: (params) =>
          params.get('cursor') === 'CURSOR-2'
            ? nextPage.promise
            : reply(page({ total: 3, next_cursor: 'CURSOR-2' })),
      });
      renderPage();
      await findResults();

      await user.click(screen.getByRole('button', { name: 'Next' }));

      expect(await screen.findByText('3 variants · updating…')).toBeInTheDocument();
      expect(screen.getByRole('link', { name: 'BRCA2' })).toBeInTheDocument();
      // The cursor on screen belongs to the page being replaced.
      expect(screen.getByRole('button', { name: 'Next' })).toBeDisabled();

      nextPage.resolve({ data: page({ total: 3, variants: [TP53], next_cursor: null }) });
      expect(await screen.findByRole('link', { name: 'TP53' })).toBeInTheDocument();
      expect(screen.getByText('3 variants')).toBeInTheDocument();
    });

    it('sorts by the clicked column, flips the direction on a second click, and restarts at page 1', async () => {
      const user = userEvent.setup();
      mockApi({ variants: () => reply(page({ total: 120, next_cursor: 'CURSOR-2' })) });
      renderPage();
      await findResults();
      await user.click(screen.getByRole('button', { name: 'Next' }));
      await waitFor(() => expect(lastVariantRequest().get('cursor')).toBe('CURSOR-2'));

      await user.click(screen.getByRole('columnheader', { name: /^Het/ }));
      await waitFor(() => expect(lastVariantRequest().get('sort')).toBe('het_samples'));
      expect(lastVariantRequest().get('order')).toBe('desc');
      expect(lastVariantRequest().has('cursor')).toBe(false);
      expect(screen.getByText('Page 1')).toBeInTheDocument();
      expect(screen.getByRole('columnheader', { name: 'Het ↓' })).toBeInTheDocument();

      await user.click(screen.getByRole('columnheader', { name: /^Het/ }));
      await waitFor(() => expect(lastVariantRequest().get('order')).toBe('asc'));
      expect(screen.getByRole('columnheader', { name: 'Het ↑' })).toBeInTheDocument();
    });
  });

  describe('carrier dialog', () => {
    it('lists the carriers of the clicked count in the selected assembly, and closes on Escape', async () => {
      const user = userEvent.setup();
      renderPage();
      await findResults();

      await user.click(within(rowOf('BRCA2')).getByRole('button', { name: '3' }));

      const dialog = await screen.findByRole('dialog', { name: 'BRCA2' });
      expect(within(dialog).getByText('Heterozygous carriers')).toBeInTheDocument();
      expect(await within(dialog).findByRole('link', { name: 'F1' })).toHaveAttribute(
        'href',
        '/families/F1',
      );
      expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, {
        params: { assembly_id: 'asm-38', genotype: 'het' },
      });

      await user.keyboard('{Escape}');
      expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    });

    it('counts imputed calls in the table and the carrier dialog alike', async () => {
      const user = userEvent.setup();
      renderPage();
      await findResults();

      await user.click(screen.getByRole('checkbox', { name: /Include imputed variants/ }));
      await waitFor(() => expect(lastVariantRequest().get('include_imputed')).toBe('true'));

      await user.click(within(rowOf('BRCA2')).getByRole('button', { name: '5' }));
      expect(await screen.findByRole('dialog', { name: 'BRCA2' })).toBeInTheDocument();
      await waitFor(() =>
        expect(apiGetMock).toHaveBeenCalledWith(CARRIERS_URL, {
          params: { assembly_id: 'asm-38', include_imputed: 'true' },
        }),
      );
    });
  });

  describe('sample genotype filter', () => {
    it('drafts samples as Het + Hom, once each, and applies them only on Apply', async () => {
      const user = userEvent.setup();
      renderPage();
      await findResults();
      const card = genotypeCard();
      const sampleInput = card.getByPlaceholderText('add sample…');
      const apply = card.getByRole('button', { name: 'Apply' });
      expect(card.getByRole('button', { name: 'Add' })).toBeDisabled();
      expect(apply).toBeDisabled();
      expect(card.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();

      await user.type(sampleInput, '   ');
      expect(card.getByRole('button', { name: 'Add' })).toBeDisabled();
      await user.clear(sampleInput);

      await user.type(sampleInput, 'S1');
      await user.click(card.getByRole('button', { name: 'Add' }));
      const genotype = card.getByLabelText('Genotype for S1') as HTMLSelectElement;
      expect(genotype.value).toBe('het_hom');
      expect([...genotype.options].map((option) => option.textContent)).toEqual([
        'Het',
        'Hom',
        'Het + Hom',
      ]);
      expect(sampleInput).toHaveValue('');
      expect(apply).toBeEnabled();
      // A drafted row changes nothing until it is applied.
      expect(lastVariantRequest().has('sample_gt')).toBe(false);

      await user.type(sampleInput, ' S1 {Enter}');
      expect(card.getAllByLabelText('Genotype for S1')).toHaveLength(1);
      expect(sampleInput).toHaveValue('');

      await user.selectOptions(genotype, 'hom');
      await user.click(apply);
      await waitFor(() => expect(lastVariantRequest().getAll('sample_gt')).toEqual(['S1:hom']));
      expect(apply).toBeDisabled();
    });

    it('removes a sample on the next Apply, and Clear drops them all at once', async () => {
      const user = userEvent.setup();
      renderPage();
      await findResults();
      const card = genotypeCard();
      const sampleInput = card.getByPlaceholderText('add sample…');
      const apply = card.getByRole('button', { name: 'Apply' });

      await user.type(sampleInput, 'S1{Enter}');
      await user.type(sampleInput, 'S2{Enter}');
      // Each sample keeps its own genotype: changing one leaves the other as it was.
      await user.selectOptions(card.getByLabelText('Genotype for S2'), 'het');
      await user.click(apply);
      await waitFor(() =>
        expect(lastVariantRequest().getAll('sample_gt')).toEqual(['S1:het_hom', 'S2:het']),
      );

      await user.click(card.getByRole('button', { name: 'Remove S1' }));
      expect(card.queryByLabelText('Genotype for S1')).not.toBeInTheDocument();
      expect(lastVariantRequest().getAll('sample_gt')).toEqual(['S1:het_hom', 'S2:het']);
      await user.click(apply);
      await waitFor(() => expect(lastVariantRequest().getAll('sample_gt')).toEqual(['S2:het']));

      await user.click(card.getByRole('button', { name: 'Clear' }));
      await waitFor(() => expect(lastVariantRequest().has('sample_gt')).toBe(false));
      expect(card.queryByLabelText('Genotype for S2')).not.toBeInTheDocument();
      expect(card.queryByRole('button', { name: 'Clear' })).not.toBeInTheDocument();
    });
  });

  describe('annotation filters', () => {
    it('leaves out the family-specific controls', async () => {
      renderPage();
      await findResults();

      expect(screen.getByRole('button', { name: 'Apply filters' })).toBeInTheDocument();
      expect(screen.queryByLabelText('Preset or saved search')).not.toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'Save current' })).not.toBeInTheDocument();
      expect(
        screen.queryByRole('checkbox', { name: 'Phenotype prioritization' }),
      ).not.toBeInTheDocument();
      expect(screen.queryByLabelText('Quick inheritance')).not.toBeInTheDocument();
    });

    it('applies gene and panel filters from the first page, and clears them', async () => {
      const user = userEvent.setup();
      mockApi({ variants: () => reply(page({ total: 120, next_cursor: 'CURSOR-2' })) });
      renderPage();
      await findResults();
      await user.click(screen.getByRole('button', { name: 'Next' }));
      await waitFor(() => expect(lastVariantRequest().get('cursor')).toBe('CURSOR-2'));

      fireEvent.change(screen.getByPlaceholderText(/^Gene list:/), { target: { value: 'BRCA2' } });
      await user.selectOptions(screen.getByLabelText('Quick gene panel'), 'panel-1');
      // Drafted filters change nothing until they are applied.
      expect(lastVariantRequest().has('gene')).toBe(false);
      await user.click(screen.getByRole('button', { name: 'Apply filters' }));

      await waitFor(() => expect(lastVariantRequest().get('gene')).toBe('BRCA2'));
      const params = lastVariantRequest();
      expect(params.get('panel_id')).toBe('panel-1');
      expect(params.get('assembly_id')).toBe('asm-38');
      expect(params.has('cursor')).toBe(false);
      expect(screen.getByText('Page 1')).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: 'Clear all filters' }));
      await waitFor(() => expect(lastVariantRequest().has('gene')).toBe(false));
      expect(lastVariantRequest().has('panel_id')).toBe(false);
    });
  });

  describe('CSV export', () => {
    const originalCreateObjectURL = window.URL.createObjectURL;
    const originalRevokeObjectURL = window.URL.revokeObjectURL;

    afterEach(() => {
      Object.assign(window.URL, {
        createObjectURL: originalCreateObjectURL,
        revokeObjectURL: originalRevokeObjectURL,
      });
      vi.restoreAllMocks();
    });

    it('downloads every filtered variant with the filters of the table', async () => {
      const user = userEvent.setup();
      const createObjectURL = vi.fn(() => 'blob:variant-explorer');
      const revokeObjectURL = vi.fn();
      Object.assign(window.URL, { createObjectURL, revokeObjectURL });
      // jsdom cannot follow a blob: download; the click on the link is what matters.
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
      renderPage();
      await findResults();
      await user.click(screen.getByRole('checkbox', { name: /Include imputed variants/ }));
      await waitFor(() => expect(lastVariantRequest().get('include_imputed')).toBe('true'));

      await user.click(screen.getByRole('button', { name: 'Download CSV' }));

      await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
      const tableUrl = variantUrls()[variantUrls().length - 1];
      expect(apiGetMock).toHaveBeenCalledWith(
        tableUrl.replace('/small-variants?', '/small-variants/export?'),
        { responseType: 'blob' },
      );
      const link = click.mock.contexts[0] as HTMLAnchorElement;
      expect(link.download).toBe('variant-explorer-asm-38.csv');
      expect(link.getAttribute('href')).toBe('blob:variant-explorer');
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:variant-explorer');
      expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
    });

    it('shows that the export is being prepared, and says so when it fails', async () => {
      const user = userEvent.setup();
      const exportReply = deferred();
      mockApi({ exportCsv: () => exportReply.promise });
      renderPage();
      await findResults();

      await user.click(screen.getByRole('button', { name: 'Download CSV' }));
      expect(await screen.findByRole('button', { name: 'Preparing…' })).toBeDisabled();

      exportReply.reject(new Error('Request failed with status code 504'));
      expect(
        await screen.findByText('Could not export variants. Try narrowing your filters and retry.'),
      ).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'Download CSV' })).toBeEnabled();
    });
  });
});
