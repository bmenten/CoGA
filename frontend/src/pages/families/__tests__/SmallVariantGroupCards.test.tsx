import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmallVariantGroupCards from '../SmallVariantGroupCards';
import {
  ACMG_CLASSIFICATION_LABELS,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantCarrierGene,
  type SmallVariantGroup,
  type SmallVariantResultGroup,
} from '../smallVariantSearch';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { BUILT_IN_REVIEW_TAGS } from '../../../test/tagDefinitions';
import { compoundHetReview, genotype, smallVariant, smallVariantGroup } from '../../../test/variants';
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

const PROBAND = [{ sample_id: 'PROBAND', role: 'proband', affected: true } as FamilyMember];

const renderGroups = (
  groups: SmallVariantResultGroup[],
  { members = PROBAND, memberView = 'cards' }: { members?: FamilyMember[]; memberView?: 'cards' | 'table' } = {},
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

const renderPair = (group: Partial<SmallVariantGroup>, options?: { memberView?: 'cards' | 'table' }) =>
  renderGroups(
    [
      smallVariantGroup({
        group_key: 'pair-1',
        gene: 'TRNT1',
        variants: [
          variant('v1', '0|1', 3129108, { hgvsc: 'NM_182916.3:c.497T>C', hgvsp: 'NP_886552.3:p.Leu166Ser' }),
          variant('v2', '1|0', 3139593, { hgvsc: 'NM_182916.3:c.1246A>G', hgvsp: 'NP_886552.3:p.Lys416Glu' }),
        ],
        ...group,
      }),
    ],
    options,
  );

const phasingRow = () =>
  screen.getByText('Phasing').closest('.variant-compound-het-summary-row') as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  mockApi();
});

describe('SmallVariantGroupCards pair phasing', () => {
  it('marks a read-backed trans pair', () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    expect(within(phasingRow()).getByText('In trans · read-backed')).toBeInTheDocument();
  });

  it('marks a pair the parents place in trans as such, not as read-backed', () => {
    renderPair({ phase: 'trans', phase_evidence: 'segregation' });
    expect(within(phasingRow()).getByText('In trans · by segregation')).toBeInTheDocument();
    expect(within(phasingRow()).queryByText(/read-backed/)).not.toBeInTheDocument();
  });

  it('does not claim evidence it was not given', () => {
    renderPair({ phase: 'trans' });
    expect(within(phasingRow()).getByText('In trans')).toBeInTheDocument();
  });

  it('says so plainly when neither the reads nor the family resolved the pair', () => {
    renderPair({ phase: 'unknown' });
    expect(within(phasingRow()).getByText('Not resolved by reads or segregation')).toBeInTheDocument();
  });

  it('treats a payload without a phase as unresolved rather than claiming trans', () => {
    // The list always sends a phase (SmallVariantGroupOut); a pair without one claims no trans.
    renderPair({ phase: undefined });
    expect(within(phasingRow()).getByText('Not resolved by reads or segregation')).toBeInTheDocument();
  });

  it('keeps the curator phase separate from what the reads say', () => {
    renderPair({
      phase: 'trans',
      phase_evidence: 'read',
      // The first variant's pair review, which names its partner.
      review: compoundHetReview({ group_id: 'G1', partner_variant_ids: ['v2'], phase_status: 'likely_cis' }),
    });
    // The reviewer's own call stands on its own row and is not overwritten.
    const curated = screen.getByText('Phase').closest('.variant-compound-het-summary-row') as HTMLElement;
    expect(within(curated).getByText('Likely cis')).toBeInTheDocument();
    expect(within(phasingRow()).getByText('In trans · read-backed')).toBeInTheDocument();
  });
});

// The pair is still a pair: one frame headed by its gene, with each of its variants as the full
// card the single-variant list shows.
describe('SmallVariantGroupCards pair variants', () => {
  it('shows each variant of the pair as its full card, headed by its place and its change', async () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    const pair = screen.getByRole('article', { name: 'Compound het pair in TRNT1' });
    expect(within(pair).getByText('Compound het pair')).toBeInTheDocument();
    expect(within(pair).getByText('2 variants')).toBeInTheDocument();

    const cards = pair.querySelectorAll('article.small-variant-card');
    expect(cards).toHaveLength(2);
    const [first, second] = Array.from(cards) as HTMLElement[];
    expect(within(first).getByText('Variant 1 of 2')).toBeInTheDocument();
    expect(within(first).getByText('c.497T>C · p.Leu166Ser')).toBeInTheDocument();
    expect(within(second).getByText('Variant 2 of 2')).toBeInTheDocument();
    expect(within(second).getByText('c.1246A>G · p.Lys416Glu')).toBeInTheDocument();
    // Everything the single card shows: its columns and its review actions.
    for (const card of [first, second]) {
      expect(within(card).getByText('Genotypes')).toBeInTheDocument();
      expect(within(card).getByText('Frequencies')).toBeInTheDocument();
      expect(within(card).getByText('In silico')).toBeInTheDocument();
      expect(within(card).getByRole('button', { name: 'ACMG classify' })).toBeInTheDocument();
      expect(within(card).getByRole('button', { name: 'Tags & notes' })).toBeInTheDocument();
      expect(within(card).getByText('More annotations & detail')).toBeInTheDocument();
    }
  });

  it('names the gene and its diseases once, for the pair', async () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    const pair = screen.getByRole('article', { name: 'Compound het pair in TRNT1' });
    expect(await within(pair).findByText('Sideroblastic anemia with immunodeficiency')).toBeInTheDocument();
    expect(within(pair).getAllByText('Sideroblastic anemia with immunodeficiency')).toHaveLength(1);
    // The cards inside are headed by their change, not by the gene again.
    for (const card of Array.from(pair.querySelectorAll('article.small-variant-card'))) {
      expect(within(card as HTMLElement).queryByRole('link', { name: 'TRNT1' })).not.toBeInTheDocument();
    }
  });

  it('folds one variant of the pair to its heading, leaving the other open', async () => {
    renderPair({ phase: 'trans' });
    const pair = screen.getByRole('article', { name: 'Compound het pair in TRNT1' });
    await userEvent.click(
      within(pair).getByRole('button', { name: 'Collapse the card of variant 1 of 2 in TRNT1' }),
    );
    const [first, second] = Array.from(pair.querySelectorAll('article.small-variant-card')) as HTMLElement[];
    expect(first).toHaveClass('variant-card--collapsed');
    // Its heading still tells it apart.
    expect(within(first).getByText('c.497T>C · p.Leu166Ser')).toBeInTheDocument();
    expect(within(first).queryByText('Genotypes')).not.toBeInTheDocument();
    expect(second).not.toHaveClass('variant-card--collapsed');
  });

  it('lists the pair as table rows in the table view, still in its frame', () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' }, { memberView: 'table' });
    const pair = screen.getByRole('article', { name: 'Compound het pair in TRNT1' });
    expect(within(pair).getByRole('table')).toBeInTheDocument();
    expect(pair.querySelectorAll('article.small-variant-card')).toHaveLength(0);
    expect(within(phasingRow()).getByText('In trans · read-backed')).toBeInTheDocument();
  });
});

// A pair card folds to its top bar from the caret before it, and opens again from it.
describe('SmallVariantGroupCards collapse', () => {
  it('folds the pair to its top bar and the changes of its variants, and opens it again', async () => {
    renderPair({ phase: 'trans', phase_evidence: 'read' });
    const card = screen.getByRole('article', { name: 'Compound het pair in TRNT1' });

    await userEvent.click(
      within(card).getByRole('button', { name: 'Collapse the card of the compound het pair in TRNT1' }),
    );
    expect(card).toHaveClass('variant-card--collapsed');
    expect(card.querySelector('.variant-card-body')).toBeNull();
    expect(within(card).getByRole('button', { name: 'Review pair' })).toBeInTheDocument();
    expect(within(card).getByText(/c\.497T>C · p\.Leu166Ser\s+\+\s+c\.1246A>G · p\.Lys416Glu/)).toBeInTheDocument();

    await userEvent.click(
      within(card).getByRole('button', { name: 'Expand the card of the compound het pair in TRNT1' }),
    );
    expect(card.querySelector('.variant-card-body')).not.toBeNull();
  });
});

// The pair review panel shows the pair's class by the one rule of the family page, once: text
// "VUS" beside the tag of class 4 shows one class, not the text beside every class tag.
describe('SmallVariantGroupCards pair review class', () => {
  const classLabels = Object.values(ACMG_CLASSIFICATION_LABELS).map((label) => label.toLowerCase());
  const classesShown = (shown: string) => {
    const panel = document.querySelector('.variant-group-panel') as HTMLElement;
    return [
      ...new Set(
        Array.from(panel.querySelectorAll('.variant-card-chip'))
          .map((chip) => chip.textContent ?? '')
          .filter((text) => classLabels.includes(text.toLowerCase()) || (shown !== '' && text === shown)),
      ),
    ];
  };

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
      Array.from(card.querySelectorAll('.variant-card-headline .variant-group-partner')).map((chip) => chip.textContent);
    expect(cards.map(carrierChips)).toEqual([['WOMAN'], ['MAN'], ['WOMAN']]);
  });

  it('says what each partner carries in the gene, and why the couple is at risk', () => {
    renderGroups([cftr()], { members: COUPLE });
    const panel = document.querySelector('.variant-group-panel') as HTMLElement;
    expect(within(panel).getByText('Both partners carry a variant in CFTR: a child can inherit one from each.')).toBeInTheDocument();
    const rows = Array.from(panel.querySelectorAll('.variant-compound-het-summary-row')).map((row) => row.textContent);
    expect(rows).toEqual(['WOMAN (mother)carries variants 1 and 3', 'MAN (father)carries variant 2']);
  });

  it('names the variants by their change in the table view', () => {
    renderGroups([cftr()], { members: COUPLE, memberView: 'table' });
    const panel = document.querySelector('.variant-group-panel') as HTMLElement;
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
