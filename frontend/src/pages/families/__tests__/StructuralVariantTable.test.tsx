// The structural-variant results table (#526: 16 % covered): the columns shown and their
// sort, how each value is written, the review cell's quick tags and chips, the genotypes,
// and the IGV / chromosome-view links, including the BND partner's.
import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import StructuralVariantTable, { formatStructuralLength } from '../StructuralVariantTable';
import type { StructuralVariant } from '../structuralVariantSearch';
import type { SmallVariantTagDefinition } from '../smallVariantSearch';

const ALL_COLUMNS = {
  chr: true,
  start: true,
  end: true,
  length: true,
  type: true,
  source: true,
  gene: true,
  cytoband: true,
  inheritance: true,
  control_af: true,
  phenotype: true,
  region_flags: true,
  qual: true,
  read_support: true,
  filter: true,
  remote_chr: true,
  remote_start: true,
  genotypes: true,
};

const TAGS: SmallVariantTagDefinition[] = [
  { key: 'review', label: 'Review', group: 'collaboration', color: '#111111', sort_order: 1, scope: 'system', is_custom: false },
  { key: 'excluded', label: 'Excluded', group: 'collaboration', color: '#222222', sort_order: 2, scope: 'system', is_custom: false },
  { key: 'report', label: 'Report', group: 'collaboration', color: '#333333', sort_order: 3, scope: 'system', is_custom: false },
  { key: 'mosaic', label: 'Mosaic?', group: 'custom', color: '#7c3aed', sort_order: 10, scope: 'global', is_custom: true },
  { key: 'acmg_class_4', label: 'Likely Pathogenic - class 4', group: 'classification', color: '#dc2626', sort_order: 20, scope: 'system', is_custom: false },
];

const sv = (overrides: Partial<StructuralVariant> = {}): StructuralVariant => ({
  _id: 'sv1',
  chr: '13',
  start: 32_315_000,
  end: 32_400_000,
  length: -85_000,
  type: 'DEL',
  source: 'sniffles',
  gene: 'BRCA2',
  genotypes: [
    { sample: 'CHILD', gt: '0/1', qual: 60, read_support: 12, filter: 'PASS' },
    { sample: 'MOTHER', gt: '0/0', qual: 55, filter: 'PASS' },
  ],
  ...overrides,
});

const renderTable = (props: Partial<React.ComponentProps<typeof StructuralVariantTable>> = {}) => {
  const handlers = {
    onSort: vi.fn(),
    onEditReview: vi.fn(),
    onClassifyCnv: vi.fn(),
    onToggleReviewTag: vi.fn(async () => undefined),
  };
  const view = render(
    <MemoryRouter>
      <StructuralVariantTable
        familyId="F1"
        projectId="P1"
        linkSearch="?type=DEL"
        members={[
          { sample_id: 'CHILD', role: 'proband', affected: true, sex: 'female' },
          { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
        ]}
        variants={[sv()]}
        sortKey="start"
        sortAsc
        visible={ALL_COLUMNS}
        tags={TAGS}
        {...handlers}
        {...props}
      />
    </MemoryRouter>,
  );
  return { ...view, handlers };
};

const row = () => screen.getAllByRole('row')[1];

describe('formatStructuralLength', () => {
  it.each([
    [150, '150 bp'],
    [-150, '-150 bp'],
    [1_000, '1000 bp'],
    [1_500, '1.5 kb'],
    [-85_000, '-85.0 kb'],
    [2_345_678, '2.3 Mb'],
    [null, '—'],
    [Number.NaN, '—'],
  ])('writes %s as %s', (length, expected) => {
    expect(formatStructuralLength(length)).toBe(expected);
  });

  it('writes lengths up to 1 kb in bp, as the report narrative does', () => {
    // From 100 bp on, one decimal of kb read a 150 bp SV as "0.1 kb".
    expect(formatStructuralLength(150)).not.toMatch(/kb/);
    expect(formatStructuralLength(999)).toBe('999 bp');
  });
});

describe('StructuralVariantTable', () => {
  it('shows the chosen columns, marks the sort, and sorts by the header clicked', () => {
    const { handlers } = renderTable({
      visible: { ...ALL_COLUMNS, source: false, qual: false },
      hasPriority: true,
    });
    const headers = screen.getAllByRole('columnheader').map((header) => header.textContent);
    expect(headers).toContain('Start ▲');
    expect(headers).not.toContain('Source ');
    expect(headers).not.toContain('QUAL ');
    expect(headers[0]).toMatch(/^Score/);

    fireEvent.click(screen.getByText(/^Length/));
    expect(handlers.onSort).toHaveBeenCalledWith('length');
  });

  it('marks a descending sort', () => {
    renderTable({ sortKey: 'length', sortAsc: false });
    expect(screen.getByText('Length ▼')).toBeInTheDocument();
  });

  it('writes each value, and a dash where there is none', () => {
    renderTable({
      variants: [
        sv({
          annotation_extra: {
            cytoband: '13q13.1',
            inheritance: 'de novo',
            control_af: 0.0004,
            omim_phenotype: 'Breast-ovarian cancer, familial, 2'.repeat(4),
            hpo_terms: 'HP:0003002, HP:0100615',
            region_flags: ['segdup'],
          },
          gene_pli: 0.9876,
        }),
      ],
    });
    const cells = within(row()).getAllByRole('cell').map((cell) => cell.textContent);
    expect(cells).toEqual(
      expect.arrayContaining(['13', '32315000', '32400000', '-85.0 kb', 'DEL', 'sniffles', 'BRCA2', '13q13.1', 'de novo', '4.0e-4']),
    );
    // A long phenotype is shortened, with the whole of it in the tooltip.
    const phenotype = within(row()).getByTitle('Breast-ovarian cancer, familial, 2'.repeat(4));
    expect(phenotype.textContent?.endsWith('…')).toBe(true);
    expect(within(row()).getByText('HPO HP:0003002, HP:0100615')).toBeInTheDocument();
    expect(within(row()).getByText('segdup, pLI 0.988')).toBeInTheDocument();
    // Per-sample metrics, and a dash for one the caller did not give.
    expect(within(row()).getByText('CHILD: 12')).toBeInTheDocument();
    expect(within(row()).getByText('MOTHER: —')).toBeInTheDocument();
  });

  it('writes a dash for what is missing', () => {
    renderTable({ variants: [sv({ source: undefined, gene: undefined, type: '', annotation_extra: undefined })] });
    const cells = within(row()).getAllByRole('cell').map((cell) => cell.textContent);
    // Type, source, gene, band, inheritance, control AF, phenotype, regions, remote chr and start.
    expect(cells.filter((text) => text === '—').length).toBeGreaterThanOrEqual(9);
  });

  it('toggles the quick tags, shows which are on, and leaves them off while a save is pending', () => {
    const { handlers, rerender } = renderTable({
      variants: [sv({ review: { tags: ['review', 'report'] } as StructuralVariant['review'] })],
    });
    const review = screen.getByRole('button', { name: 'Review' });
    const exclude = screen.getByRole('button', { name: 'Exclude' });
    const report = screen.getByRole('button', { name: 'Report' });
    // Not by the tint alone: the state is announced (#529).
    expect(review).toHaveAttribute('aria-pressed', 'true');
    expect(exclude).toHaveAttribute('aria-pressed', 'false');
    expect(report).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(exclude);
    expect(handlers.onToggleReviewTag).toHaveBeenCalledWith(expect.objectContaining({ _id: 'sv1' }), 'excluded');
    fireEvent.click(screen.getByRole('button', { name: 'ACMG (CNV)' }));
    expect(handlers.onClassifyCnv).toHaveBeenCalledWith(expect.objectContaining({ _id: 'sv1' }));
    fireEvent.click(screen.getByRole('button', { name: 'More tags' }));
    expect(handlers.onEditReview).toHaveBeenCalledWith(expect.objectContaining({ _id: 'sv1' }));

    rerender(
      <MemoryRouter>
        <StructuralVariantTable
          familyId="F1"
          linkSearch=""
          members={[]}
          variants={[sv()]}
          sortKey="start"
          sortAsc
          visible={ALL_COLUMNS}
          tags={TAGS}
          onSort={vi.fn()}
          reviewIsPending
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );
    expect(screen.getByRole('button', { name: 'Review' })).toBeDisabled();
  });

  it('greys out an excluded SV and shows its other tags, classification and note', () => {
    renderTable({
      variants: [
        sv({
          review: {
            tags: ['excluded', 'mosaic'],
            classification: 'VUS - class 3',
            note: 'Breakpoint in intron 10.',
          } as StructuralVariant['review'],
        }),
      ],
    });
    expect(row()).toHaveClass('variant-table-row--excluded');
    // The quick tags are buttons, not chips; the custom tag is a chip.
    expect(within(row()).getByText('Mosaic?')).toHaveClass('table-chip--tag');
    expect(within(row()).queryByText('Excluded')).not.toBeInTheDocument();
    expect(within(row()).getByText('VUS - class 3')).toHaveClass('table-chip--warning');
    expect(within(row()).getByText('Breakpoint in intron 10.')).toBeInTheDocument();
  });

  it('shows a classification once, as its tag when it is tagged', () => {
    renderTable({
      variants: [
        sv({
          review: { tags: ['acmg_class_4'], classification: 'Likely Pathogenic - class 4' } as StructuralVariant['review'],
        }),
      ],
    });
    expect(within(row()).getAllByText('Likely Pathogenic - class 4')).toHaveLength(1);
  });

  it('marks the genotypes of the affected and the proband', () => {
    renderTable();
    const child = within(row()).getByText('CHILD').closest('.variant-table-genotype-item');
    const mother = within(row()).getByText('MOTHER').closest('.variant-table-genotype-item');
    expect(child).toHaveClass('variant-table-genotype-item--affected', 'variant-table-genotype-item--proband');
    expect(mother).not.toHaveClass('variant-table-genotype-item--affected');
    expect(child?.textContent).toContain('Het');
  });

  it('links IGV to the SV with its flank, and a BND partner to its own breakpoint', () => {
    renderTable({
      variants: [sv({ type: 'BND', start: 5_000, end: 5_000, length: 0, remote_chr: '7', remote_start: 1_500 })],
    });
    const igv = within(row()).getAllByRole('link', { name: /Open IGV at/ });
    expect(igv).toHaveLength(2);
    const [primary, partner] = igv.map((link) => new URL(link.getAttribute('href') ?? '', 'http://x'));
    expect(primary.searchParams.get('locus')).toBe('chr13:4000-6000');
    expect(primary.searchParams.get('project_id')).toBe('P1');
    expect(primary.searchParams.get('back_path')).toBe('/families/F1/structural-variants?type=DEL');
    // The partner gets the same 1 kb flank, clamped at the chromosome start.
    expect(partner.searchParams.get('locus')).toBe('chr7:500-2500');
    expect(within(row()).getByRole('link', { name: /Open chromosome view around 13:5000-5000/ })).toHaveAttribute(
      'href',
      '/families/F1/chromosome/13?start=0&end=55000&type=DEL&project_id=P1',
    );
  });

  it('has no partner link without a partner', () => {
    renderTable();
    expect(within(row()).getAllByRole('link', { name: /Open IGV at/ })).toHaveLength(1);
  });

  it('says so when nothing matches', () => {
    renderTable({ variants: [] });
    expect(screen.getByText('No structural variants match the current search.')).toBeInTheDocument();
  });

  it('keeps two callers of one SV as two rows, each with its own call', () => {
    // A per-sample upload's id names no caller, so a Sniffles and a Spectre call at the same
    // coordinates share it and come back as two rows. Keyed by the id alone, React warned of a
    // duplicate key and could drop or reuse a row on update.
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    const sniffles = sv({ _id: '13-32315000-32400000-DEL---', source: 'sniffles' });
    const spectre = sv({
      _id: '13-32315000-32400000-DEL---',
      source: 'spectre',
      genotypes: [{ sample: 'CHILD', gt: '1/1', filter: 'PASS' }],
    });
    const { rerender } = renderTable({ variants: [sniffles, spectre] });
    const rows = () => screen.getAllByRole('row').slice(1);
    const described = () =>
      rows().map((tableRow) => [
        within(tableRow).getByText(/^(sniffles|spectre)$/).textContent,
        within(tableRow).getByText('CHILD').closest('.variant-table-genotype-item')?.textContent,
      ]);
    expect(described()).toEqual([
      ['sniffles', 'CHILDHet'],
      ['spectre', 'CHILDHom'],
    ]);
    // Reordered: each row still shows its own caller's call.
    rerender(
      <MemoryRouter>
        <StructuralVariantTable
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[{ sample_id: 'CHILD', role: 'proband', affected: true, sex: 'female' }]}
          variants={[spectre, sniffles]}
          sortKey="start"
          sortAsc
          visible={ALL_COLUMNS}
          tags={TAGS}
          onSort={vi.fn()}
          onEditReview={vi.fn()}
          onClassifyCnv={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );
    expect(described()).toEqual([
      ['spectre', 'CHILDHom'],
      ['sniffles', 'CHILDHet'],
    ]);
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    errors.mockRestore();
  });
});
