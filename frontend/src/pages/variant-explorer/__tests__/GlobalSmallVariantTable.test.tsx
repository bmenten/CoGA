// The explorer's results table: one row per variant (gene link, locus, lab classification
// before ClinVar, tags), sortable headers, and count buttons that open the carrier dialog.

import type { ComponentProps } from 'react';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import type { SmallVariantTagDefinition } from '../../families/smallVariantSearch';
import GlobalSmallVariantTable from '../GlobalSmallVariantTable';
import type { GlobalVariantSort } from '../globalSmallVariantSearch';
import type { CarrierModalMode, GlobalVariantRow } from '../types';

const makeVariant = (overrides: Partial<GlobalVariantRow> = {}): GlobalVariantRow => ({
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
  hgvsp: 'p.Arg2336His',
  clinvar: 'Pathogenic',
  classification: null,
  tags: [],
  total_samples: 5,
  het_samples: 3,
  hom_samples: 2,
  total_families: 4,
  ...overrides,
});

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

const renderTable = (props: Partial<ComponentProps<typeof GlobalSmallVariantTable>> = {}) => {
  const onSort = vi.fn<(sort: GlobalVariantSort) => void>();
  const onOpenCarriers = vi.fn<(variant: GlobalVariantRow, mode: CarrierModalMode) => void>();
  render(
    <MemoryRouter>
      <GlobalSmallVariantTable
        variants={[makeVariant()]}
        tagDefinitions={TAGS}
        sort="total_samples"
        order="desc"
        onSort={onSort}
        onOpenCarriers={onOpenCarriers}
        {...props}
      />
    </MemoryRouter>,
  );
  return { onSort, onOpenCarriers };
};

// The body rows' cells, one array per variant.
const bodyCells = () =>
  screen
    .getAllByRole('row')
    .slice(1)
    .map((row) => within(row).getAllByRole('cell'));

const headerTexts = () => screen.getAllByRole('columnheader').map((th) => th.textContent);

describe('GlobalSmallVariantTable', () => {
  it('shows one row per variant with its gene, locus, HGVS.c, consequence and impact', () => {
    renderTable({
      variants: [
        makeVariant(),
        makeVariant({
          key: '102',
          chr: 'X',
          pos: 154030912,
          ref: 'C',
          alt: 'CT',
          type: 'INDEL',
          gene: 'MECP2',
          hgvsc: null,
          consequence: 'frameshift_variant',
          impact: 'HIGH',
        }),
      ],
    });

    expect(headerTexts()).toEqual([
      'Gene',
      'Variant',
      'Classification',
      'Consequence',
      'Tags',
      'Total samples ↓',
      'Het',
      'Hom',
      'Families',
    ]);
    const [brca2, mecp2] = bodyCells();
    expect(bodyCells()).toHaveLength(2);

    // The gene links to the gene page for that gene.
    const geneLink = within(brca2[0]).getByRole('link', { name: 'BRCA2' });
    const [path, query = ''] = (geneLink.getAttribute('href') ?? '').split('?');
    expect(path).toBe('/genes');
    expect([...new URLSearchParams(query).values()]).toContain('BRCA2');

    expect(within(brca2[1]).getByText('13:32316461 G>A')).toBeInTheDocument();
    expect(within(brca2[1]).getByText('c.7007G>A')).toBeInTheDocument();
    expect(brca2[3]).toHaveTextContent(/^missense_variantMODERATE$/);

    expect(within(mecp2[0]).getByRole('link', { name: 'MECP2' })).toBeInTheDocument();
    expect(mecp2[1]).toHaveTextContent(/^X:154030912 C>CT$/);
    expect(mecp2[3]).toHaveTextContent(/^frameshift_variantHIGH$/);
  });

  it('shows a dash rather than a gene link for an intergenic variant', () => {
    renderTable({ variants: [makeVariant({ gene: null, gene_symbols: [], consequence: null, impact: null })] });

    const [cells] = bodyCells();
    expect(within(cells[0]).queryByRole('link')).not.toBeInTheDocument();
    expect(cells[0]).toHaveTextContent(/^—$/);
    expect(cells[3]).toHaveTextContent(/^—$/);
  });

  it('shows the lab classification before ClinVar, ClinVar when unclassified, else a dash', () => {
    renderTable({
      variants: [
        makeVariant({
          key: '1',
          classification: 'Likely Pathogenic - class 4',
          clinvar: 'Uncertain significance',
        }),
        makeVariant({ key: '2', classification: null, clinvar: 'Pathogenic' }),
        makeVariant({ key: '3', classification: null, clinvar: null }),
      ],
    });

    expect(bodyCells().map((cells) => cells[2].textContent)).toEqual([
      'Likely Pathogenic - class 4',
      'Pathogenic',
      '—',
    ]);
  });

  it('labels and colours tags from their definitions, falling back to the key and a neutral colour', () => {
    renderTable({
      variants: [
        makeVariant({ key: '1', tags: ['review', 'lab_custom_tag'] }),
        makeVariant({ key: '2', tags: [] }),
      ],
    });

    const [tagged, untagged] = bodyCells();
    expect(within(tagged[4]).getByText('Review')).toHaveStyle({ backgroundColor: '#2563eb' });
    expect(within(tagged[4]).getByText('lab_custom_tag')).toHaveStyle({ backgroundColor: '#5b6b79' });
    expect(untagged[4]).toHaveTextContent(/^—$/);
  });

  it('opens the carrier dialog in the mode of the count that was clicked', async () => {
    const user = userEvent.setup();
    const variant = makeVariant();
    const { onOpenCarriers } = renderTable({ variants: [variant] });
    const [cells] = bodyCells();

    await user.click(within(cells[5]).getByRole('button', { name: '5' }));
    expect(onOpenCarriers).toHaveBeenLastCalledWith(variant, 'all');
    await user.click(within(cells[6]).getByRole('button', { name: '3' }));
    expect(onOpenCarriers).toHaveBeenLastCalledWith(variant, 'het');
    await user.click(within(cells[7]).getByRole('button', { name: '2' }));
    expect(onOpenCarriers).toHaveBeenLastCalledWith(variant, 'hom');
    await user.click(within(cells[8]).getByRole('button', { name: '4' }));
    expect(onOpenCarriers).toHaveBeenLastCalledWith(variant, 'families');
    expect(onOpenCarriers).toHaveBeenCalledTimes(4);
  });

  it('shows a zero count as plain text that opens nothing', () => {
    renderTable({
      variants: [
        makeVariant({ key: '1', total_samples: 2, het_samples: 2, hom_samples: 0, total_families: 1 }),
        makeVariant({ key: '2', total_samples: 0, het_samples: 0, hom_samples: 0, total_families: 0 }),
      ],
    });

    const [hetOnly, none] = bodyCells();
    expect(within(hetOnly[6]).getByRole('button', { name: '2' })).toBeInTheDocument();
    expect(within(hetOnly[7]).queryByRole('button')).not.toBeInTheDocument();
    expect(hetOnly[7]).toHaveTextContent(/^0$/);
    for (const cell of none.slice(5)) {
      expect(within(cell).queryByRole('button')).not.toBeInTheDocument();
      expect(cell).toHaveTextContent(/^0$/);
    }
  });

  it('sorts from the gene and count headers only', async () => {
    const user = userEvent.setup();
    const { onSort } = renderTable();

    for (const header of [/^Gene/, /^Total samples/, /^Het/, /^Hom/, /^Families/]) {
      await user.click(screen.getByRole('columnheader', { name: header }));
    }
    // The gene column orders the table by genomic position.
    expect(onSort.mock.calls.map(([sort]) => sort)).toEqual([
      'position',
      'total_samples',
      'het_samples',
      'hom_samples',
      'total_families',
    ]);

    for (const header of ['Variant', 'Classification', 'Consequence', 'Tags']) {
      await user.click(screen.getByRole('columnheader', { name: header }));
    }
    expect(onSort).toHaveBeenCalledTimes(5);
  });

  it.each([
    ['position', 'desc', 'Gene ↓'],
    ['total_samples', 'asc', 'Total samples ↑'],
    ['het_samples', 'asc', 'Het ↑'],
    ['hom_samples', 'desc', 'Hom ↓'],
    ['total_families', 'desc', 'Families ↓'],
  ] as const)('marks only the active %s sort, with its %s direction', (sort, order, marked) => {
    renderTable({ sort, order });

    expect(headerTexts().filter((text) => /[↑↓]/.test(text ?? ''))).toEqual([marked]);
  });
});
