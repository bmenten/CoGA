import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmallVariantGroupCards from '../SmallVariantGroupCards';
import {
  ACMG_CLASSIFICATION_LABELS,
  type CompoundHetPair,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantCarrierGene,
  type SmallVariantCompoundHetGene,
  type SmallVariantResultGroup,
} from '../smallVariantSearch';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { BUILT_IN_REVIEW_TAGS } from '../../../test/tagDefinitions';
import {
  compoundHetGene,
  compoundHetPair,
  compoundHetReview,
  genotype,
  smallVariant,
} from '../../../test/variants';
import reviewClassCases from './fixtures/review-class-cases.json';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

type ApiConfig = { params?: { symbol?: string[] } };

// GET /genes/disease-links answers one entry per gene asked; TRNT1 has a causal Monarch link.
const mockApi = () => {
  (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string, config?: ApiConfig) =>
    url === '/genes/disease-links'
      ? Promise.resolve({
          data: (config?.params?.symbol ?? []).map((symbol) => ({
            symbol,
            monarch:
              symbol === 'TRNT1'
                ? [
                    {
                      mondo_id: 'MONDO:0014000',
                      disease_label: 'Sideroblastic anemia with immunodeficiency',
                      causal: true,
                      sources: ['omim'],
                      monarch_url: 'https://monarchinitiative.org/MONDO:0014000',
                    },
                  ]
                : [],
            omim: [],
            inheritance: [],
            dosage: null,
            acmg_secondary_finding: { status: 'not_listed' },
          })),
        })
      : Promise.resolve({ data: {} }),
  );
};

const variant = (id: string, gt: string, start: number, fields: Partial<SmallVariant> = {}): SmallVariant =>
  smallVariant({
    _id: id,
    chr: '3',
    start,
    end: start,
    type: 'SNV',
    gene: 'TRNT1',
    ref: 'A',
    alt: 'G',
    impact: 'MODERATE',
    effect: 'missense_variant',
    genotypes: [genotype({ sample: 'PROBAND', gt })],
    ...fields,
  });

const TRIO = [
  { sample_id: 'PROBAND', role: 'proband', affected: true } as FamilyMember,
  { sample_id: 'FATHER1', role: 'father', affected: false } as FamilyMember,
  { sample_id: 'MOTHER', role: 'mother', affected: false } as FamilyMember,
];

const renderGroups = (
  groups: SmallVariantResultGroup[],
  { members = TRIO, memberView = 'cards' }: { members?: FamilyMember[]; memberView?: 'cards' | 'table' } = {},
) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>
        <SmallVariantGroupCards
          groups={groups}
          memberView={memberView}
          members={members}
          familyId="F1"
          projectId="P1"
          locationSearch=""
          tags={BUILT_IN_REVIEW_TAGS}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn()}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );

const V1 = variant('v1', '0|1', 3129108, { hgvsc: 'NM_182916.3:c.497T>C', hgvsp: 'NP_886552.3:p.Leu166Ser' });
const V2 = variant('v2', '1|0', 3139593, { hgvsc: 'NM_182916.3:c.1246A>G', hgvsp: 'NP_886552.3:p.Lys416Glu' });

// TRNT1 with one pair, v1 from the father and v2 from the mother.
const renderPair = (
  pair: Partial<CompoundHetPair>,
  { gene = {}, memberView = 'cards' }: { gene?: Partial<SmallVariantCompoundHetGene>; memberView?: 'cards' | 'table' } = {},
) =>
  renderGroups(
    [
      compoundHetGene({
        group_key: 'compound-het-gene:gene:trnt1',
        gene: 'TRNT1',
        variants: [V1, V2],
        pairs: [compoundHetPair(['v1', 'v2'], pair)],
        inherited_from: { v1: 'FATHER1', v2: 'MOTHER' },
        ...gene,
      }),
    ],
    { memberView },
  );

const theGene = (name = 'TRNT1') => screen.getByRole('article', { name: `Compound het gene ${name}` });
const pairLine = () => document.querySelector('.variant-group-pair') as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  mockApi();
});

describe('SmallVariantGroupCards pair phasing', () => {
  it('marks a read-backed trans pair', () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    expect(within(pairLine()).getByText('In trans · read-backed')).toBeInTheDocument();
  });

  it('marks a pair the parents place in trans as such, not as read-backed', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    expect(within(pairLine()).getByText('In trans · by segregation')).toBeInTheDocument();
    expect(within(pairLine()).queryByText(/read-backed/)).not.toBeInTheDocument();
  });

  it('does not claim evidence it was not given', () => {
    renderPair({ phase: 'trans' });
    expect(within(pairLine()).getByText('In trans')).toBeInTheDocument();
  });

  it('says so plainly when neither the reads nor the family resolved the pair', () => {
    renderPair({ phase: 'unknown' });
    expect(within(pairLine()).getByText('Not resolved by reads or segregation')).toBeInTheDocument();
  });

  it('keeps the curator phase separate from what the reads say', () => {
    renderPair({
      phase: 'trans',
      phase_evidence: 'read',
      review: compoundHetReview({ group_id: 'G1', partner_variant_ids: ['v2'], phase_status: 'likely_cis' }),
    });
    // The reviewer's own call stands apart and is not overwritten.
    expect(within(pairLine()).getByText('Recorded phase: Likely cis')).toBeInTheDocument();
    expect(within(pairLine()).getByText('In trans · read-backed')).toBeInTheDocument();
  });

  it('shows a pair without a review as its phase alone', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    expect(within(pairLine()).queryByText(/Recorded phase/)).not.toBeInTheDocument();
  });
});

// The gene is one frame headed like a single card, with each of its variants as the full card
// the single-variant list shows.
describe('SmallVariantGroupCards compound-het gene', () => {
  it('heads the gene as a single card does, linked to the gene explorer', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    const gene = theGene();
    const link = within(gene).getByRole('link', { name: 'TRNT1' });
    expect(link).toHaveClass('variant-card-gene');
    expect(link).toHaveAttribute('href', '/genes?gene=TRNT1&family_id=F1&project_id=P1');
    expect(within(gene).getByText('Compound het')).toBeInTheDocument();
    expect(within(gene).getByText('2 variants')).toBeInTheDocument();
    expect(within(gene).getByText('1 pair')).toBeInTheDocument();
    // No button that would review the first variant alone under the pair's name.
    expect(within(gene).queryByRole('button', { name: /Review pair/ })).not.toBeInTheDocument();
  });

  it('shows each variant as its full card, headed by its place and change, with its parent', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    const cards = Array.from(theGene().querySelectorAll('article.small-variant-card')) as HTMLElement[];
    expect(cards).toHaveLength(2);
    const [first, second] = cards;
    expect(within(first).getByText('Variant 1 of 2')).toBeInTheDocument();
    expect(within(first).getByText('c.497T>C · p.Leu166Ser')).toBeInTheDocument();
    expect(within(first).getByText('Paternal')).toBeInTheDocument();
    expect(within(second).getByText('Variant 2 of 2')).toBeInTheDocument();
    expect(within(second).getByText('Maternal')).toBeInTheDocument();
    for (const card of cards) {
      expect(within(card).getByText('Genotypes')).toBeInTheDocument();
      expect(within(card).getByText('Frequencies')).toBeInTheDocument();
      expect(within(card).getByText('In silico')).toBeInTheDocument();
      expect(within(card).getByRole('button', { name: 'ACMG classify' })).toBeInTheDocument();
      expect(within(card).getByRole('button', { name: 'Tags & notes' })).toBeInTheDocument();
      expect(within(card).getByText('More annotations & detail')).toBeInTheDocument();
    }
  });

  it('names the gene and its diseases once, for the gene', async () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    const gene = theGene();
    expect(await within(gene).findByText('Sideroblastic anemia with immunodeficiency')).toBeInTheDocument();
    expect(within(gene).getAllByText('Sideroblastic anemia with immunodeficiency')).toHaveLength(1);
  });

  // Two variants from the father and three from the mother: one gene of five variants and the
  // six pairs of a paternal with a maternal one, not six results repeating them.
  it('lists five variants once, by the parent each came from, with their six pairs', () => {
    const paternal = ['f1', 'f2'];
    const maternal = ['m1', 'm2', 'm3'];
    const ids = ['f1', 'm1', 'f2', 'm2', 'm3'];
    renderGroups([
      compoundHetGene({
        group_key: 'compound-het-gene:gene:trnt1',
        gene: 'TRNT1',
        variants: ids.map((id, index) => variant(id, '0/1', 3_100_000 + 1_000 * index)),
        pairs: paternal.flatMap((f) =>
          maternal.map((m) => {
            const pair = [f, m].sort((a, b) => ids.indexOf(a) - ids.indexOf(b)) as [string, string];
            return compoundHetPair(pair, { phase: 'trans', phase_evidence: 'segregation' });
          }),
        ),
        inherited_from: Object.fromEntries([
          ...paternal.map((id) => [id, 'FATHER1']),
          ...maternal.map((id) => [id, 'MOTHER']),
        ]),
      }),
    ]);
    const gene = theGene();
    expect(within(gene).getByText('5 variants')).toBeInTheDocument();
    expect(within(gene).getByText('6 pairs')).toBeInTheDocument();
    expect(gene.querySelectorAll('article.small-variant-card')).toHaveLength(5);
    const lines = Array.from(gene.querySelectorAll('.variant-group-summary .variant-compound-het-summary-row')).map(
      (row) => row.textContent,
    );
    expect(lines).toEqual(['PaternalFATHER1 (father) gave variants 1 and 3', 'MaternalMOTHER (mother) gave variants 2, 4 and 5']);
    expect(Array.from(gene.querySelectorAll('.variant-group-pair-name')).map((name) => name.textContent)).toEqual([
      'Variants 1 + 2',
      'Variants 1 + 4',
      'Variants 1 + 5',
      'Variants 2 + 3',
      'Variants 3 + 4',
      'Variants 3 + 5',
    ]);
  });

  it('lists the variants no parent was traced for apart', () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' }, { gene: { inherited_from: {} } });
    const gene = theGene();
    const lines = Array.from(gene.querySelectorAll('.variant-group-summary .variant-compound-het-summary-row')).map(
      (row) => row.textContent,
    );
    expect(lines).toEqual(['Parent not tracedvariants 1 and 2']);
    expect(within(gene).queryByText('Paternal')).not.toBeInTheDocument();
  });

  it('folds one card of the gene to its heading, leaving the other open', async () => {
    renderPair({ phase: 'trans' });
    const gene = theGene();
    await userEvent.click(within(gene).getByRole('button', { name: 'Collapse the card of variant 1 of 2 in TRNT1' }));
    const [first, second] = Array.from(gene.querySelectorAll('article.small-variant-card')) as HTMLElement[];
    expect(first).toHaveClass('variant-card--collapsed');
    expect(within(first).getByText('c.497T>C · p.Leu166Ser')).toBeInTheDocument();
    expect(within(first).queryByText('Genotypes')).not.toBeInTheDocument();
    expect(second).not.toHaveClass('variant-card--collapsed');
  });

  it('folds the gene to its heading and the changes of its variants, and opens it again', async () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    const gene = theGene();
    await userEvent.click(within(gene).getByRole('button', { name: 'Collapse the card of the compound het gene TRNT1' }));
    expect(gene).toHaveClass('variant-card--collapsed');
    expect(gene.querySelector('.variant-card-body')).toBeNull();
    expect(within(gene).getByText(/c\.497T>C · p\.Leu166Ser\s+\+\s+c\.1246A>G · p\.Lys416Glu/)).toBeInTheDocument();
    await userEvent.click(within(gene).getByRole('button', { name: 'Expand the card of the compound het gene TRNT1' }));
    expect(gene.querySelector('.variant-card-body')).not.toBeNull();
  });

  it('lists the gene as table rows in the table view, naming the variants by their change', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' }, { memberView: 'table' });
    const gene = theGene();
    expect(within(gene).getByRole('table')).toBeInTheDocument();
    expect(gene.querySelectorAll('article.small-variant-card')).toHaveLength(0);
    expect(within(pairLine()).getByText('c.497T>C · p.Leu166Ser + c.1246A>G · p.Lys416Glu')).toBeInTheDocument();
    expect(within(gene).getByText('FATHER1 (father) gave c.497T>C · p.Leu166Ser')).toBeInTheDocument();
  });
});

// A pair's review shows the pair's class by the one rule of the family page, once: text "VUS"
// beside the tag of class 4 shows one class, not the text beside every class tag.
describe('SmallVariantGroupCards pair review class', () => {
  const classLabels = Object.values(ACMG_CLASSIFICATION_LABELS).map((label) => label.toLowerCase());
  const classesShown = (shown: string) =>
    [
      ...new Set(
        Array.from(pairLine().querySelectorAll('.variant-card-chip'))
          .map((chip) => chip.textContent ?? '')
          .filter((text) => classLabels.includes(text.toLowerCase()) || (shown !== '' && text === shown)),
      ),
    ];

  it.each(reviewClassCases.cases)('shows the class the rule gives, once: $why', ({ classification, tags, shown }) => {
    renderPair({ review: compoundHetReview({ group_id: 'g1', classification, tags }) });
    expect(classesShown(shown)).toEqual(shown ? [shown] : []);
  });
});

const COUPLE = [
  { sample_id: 'WOMAN', role: 'mother', affected: false } as FamilyMember,
  { sample_id: 'MAN', role: 'father', affected: false } as FamilyMember,
];

const coupleVariant = (id: string, gene: string, start: number, woman: string, man: string, hgvsc: string) =>
  smallVariant({
    _id: id,
    chr: '7',
    start,
    end: start,
    type: 'SNV',
    gene,
    ref: 'A',
    alt: 'G',
    impact: 'HIGH',
    effect: 'frameshift_variant',
    hgvsc,
    genotypes: [genotype({ sample: 'WOMAN', gt: woman }), genotype({ sample: 'MAN', gt: man })],
  });

const carrierGene = (fields: Partial<SmallVariantCarrierGene> & Pick<SmallVariantCarrierGene, 'variants'>) =>
  ({
    group_type: 'carrier_gene',
    group_key: 'carrier-gene:gene:CFTR',
    gene: 'CFTR',
    gene_id: null,
    partners: [],
    both_partners_carry: true,
    x_linked: false,
    ...fields,
  }) satisfies SmallVariantCarrierGene;

// An expanded carrier screen lists each gene the couple is at risk in, with every variant either
// partner carries there: two, three or more.
describe('SmallVariantGroupCards carrier genes', () => {
  const cftr = () =>
    carrierGene({
      variants: [
        coupleVariant('c1', 'CFTR', 117_500_000, '0/1', '0/0', 'NM_000492.4:c.1521_1523del'),
        coupleVariant('c2', 'CFTR', 117_550_000, '0/0', '0/1', 'NM_000492.4:c.3846G>A'),
        coupleVariant('c3', 'CFTR', 117_600_000, '0/1', '0/0', 'NM_000492.4:c.1624G>T'),
      ],
      partners: [
        { samples: ['WOMAN'], variant_ids: ['c1', 'c3'] },
        { samples: ['MAN'], variant_ids: ['c2'] },
      ],
    });

  it('lists a gene with its three variants, each as its full card with the partner who carries it', () => {
    renderGroups([cftr()], { members: COUPLE });
    const gene = screen.getByRole('article', { name: 'Carrier gene CFTR' });
    expect(within(gene).getByText('Carrier gene')).toBeInTheDocument();
    expect(within(gene).getByText('3 variants')).toBeInTheDocument();
    expect(within(gene).getByText('Both partners')).toBeInTheDocument();
    expect(within(gene).queryByText('X-linked')).not.toBeInTheDocument();
    expect(within(gene).queryByRole('button', { name: 'Review pair' })).not.toBeInTheDocument();

    const cards = Array.from(gene.querySelectorAll('article.small-variant-card')) as HTMLElement[];
    expect(cards.map((card) => within(card).getByText(/^Variant \d of 3$/).textContent)).toEqual([
      'Variant 1 of 3',
      'Variant 2 of 3',
      'Variant 3 of 3',
    ]);
    const carrierChips = (card: HTMLElement) =>
      Array.from(card.querySelectorAll('.variant-card-headline .variant-group-person')).map((chip) => chip.textContent);
    expect(cards.map(carrierChips)).toEqual([['WOMAN'], ['MAN'], ['WOMAN']]);
  });

  it('says what each partner carries in the gene, and why the couple is at risk, as plain lines', () => {
    renderGroups([cftr()], { members: COUPLE });
    const gene = screen.getByRole('article', { name: 'Carrier gene CFTR' });
    expect(within(gene).getByRole('link', { name: 'CFTR' })).toHaveClass('variant-card-gene');
    // No heading and no box of their own: the cards below are boxed already.
    expect(within(gene).queryByText('Couple')).not.toBeInTheDocument();
    expect(gene.querySelector('.variant-card-review-panel')).toBeNull();
    const panel = gene.querySelector('.variant-group-lines') as HTMLElement;
    expect(within(panel).getByText('Both partners carry a variant in CFTR: a child can inherit one from each.')).toBeInTheDocument();
    const rows = Array.from(panel.querySelectorAll('.variant-compound-het-summary-row')).map((row) => row.textContent);
    expect(rows).toEqual(['WOMAN (mother)carries variants 1 and 3', 'MAN (father)carries variant 2']);
  });

  it('names the variants by their change in the table view', () => {
    renderGroups([cftr()], { members: COUPLE, memberView: 'table' });
    const panel = document.querySelector('.variant-group-lines') as HTMLElement;
    expect(within(panel).getByText('carries c.1521_1523del and c.1624G>T')).toBeInTheDocument();
    expect(within(panel).getByText('carries c.3846G>A')).toBeInTheDocument();
  });

  it('marks an X-linked gene one partner alone carries in, and says the other carries none', () => {
    renderGroups(
      [
        carrierGene({
          group_key: 'carrier-gene:gene:DMD',
          gene: 'DMD',
          both_partners_carry: false,
          x_linked: true,
          variants: [coupleVariant('d1', 'DMD', 31_000_000, '0/1', '0/0', 'NM_004006.3:c.10G>T')],
          partners: [
            { samples: ['WOMAN'], variant_ids: ['d1'] },
            { samples: ['MAN'], variant_ids: [] },
          ],
        }),
      ],
      { members: COUPLE },
    );
    const gene = screen.getByRole('article', { name: 'Carrier gene DMD' });
    expect(within(gene).getByText('1 variant')).toBeInTheDocument();
    expect(within(gene).getByText('X-linked')).toBeInTheDocument();
    expect(within(gene).queryByText('Both partners')).not.toBeInTheDocument();
    expect(
      within(gene).getByText("On chrX outside the pseudo-autosomal regions: one partner's variant is a finding on its own."),
    ).toBeInTheDocument();
    expect(within(gene).getByText('carries no variant in DMD')).toBeInTheDocument();
  });

  it('folds a gene to its heading and the changes of its variants', async () => {
    renderGroups([cftr()], { members: COUPLE });
    const gene = screen.getByRole('article', { name: 'Carrier gene CFTR' });
    await userEvent.click(within(gene).getByRole('button', { name: 'Collapse the card of the carrier gene CFTR' }));
    expect(gene).toHaveClass('variant-card--collapsed');
    expect(gene.querySelectorAll('article.small-variant-card')).toHaveLength(0);
    expect(within(gene).getByText(/c\.1521_1523del\s+\+\s+c\.3846G>A\s+\+\s+c\.1624G>T/)).toBeInTheDocument();
  });
});
