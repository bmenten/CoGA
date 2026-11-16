import { fireEvent, render, screen, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import StructuralVariantCards from '../StructuralVariantCards';
import { ACMG_CLASSIFICATION_LABELS, type SmallVariantReview } from '../smallVariantSearch';
import type { StructuralVariant } from '../structuralVariantSearch';
import { BUILT_IN_REVIEW_TAGS } from '../../../test/tagDefinitions';
import { review, structuralVariant } from '../../../test/variants';
import reviewClassCases from './fixtures/review-class-cases.json';

const variant = structuralVariant({
  _id: '2-160120294-160120688-DEL---',
  chr: '2',
  start: 160120294,
  end: 160120688,
  length: 394,
  type: 'DEL',
  gene: 'ITGB6',
  genotypes: [],
});

const renderCards = (assembly?: { speciesName?: string; assemblyName?: string }) =>
  render(
    <MemoryRouter>
      <StructuralVariantCards
        familyId="F1"
        projectId="P1"
        linkSearch=""
        members={[]}
        variants={[variant]}
        tags={[]}
        {...assembly}
      />
    </MemoryRouter>,
  );

describe('StructuralVariantCards frequencies heading', () => {
  it('links to the gnomAD SV browser over the call span', () => {
    renderCards({ speciesName: 'Homo sapiens', assemblyName: 'GRCh38' });
    const heading = screen.getByRole('link', { name: 'Frequencies' });
    // A region view, not a variant page: gnomAD has no id matching a caller's SV.
    expect(heading).toHaveAttribute(
      'href',
      'https://gnomad.broadinstitute.org/region/2-160120294-160120688?dataset=gnomad_sv_r4',
    );
    expect(heading).toHaveAttribute('target', '_blank');
    expect(heading).toHaveAttribute('rel', 'noreferrer');
  });

  it('stays plain text when the assembly has no gnomAD to point at', () => {
    renderCards({ speciesName: 'Homo sapiens', assemblyName: 'T2T-CHM13v2.0' });
    expect(screen.queryByRole('link', { name: 'Frequencies' })).not.toBeInTheDocument();
    // The column itself is still there.
    expect(screen.getByText('Frequencies')).toBeInTheDocument();
  });

  it('stays plain text when no assembly is known at all', () => {
    renderCards();
    expect(screen.queryByRole('link', { name: 'Frequencies' })).not.toBeInTheDocument();
    expect(screen.getByText('Frequencies')).toBeInTheDocument();
  });
});

describe('StructuralVariantCards HPO link', () => {
  it('carries the SV page, with its project and filters, so the HPO page can return to it', () => {
    const withHpo: StructuralVariant = {
      ...variant,
      annotation_extra: { hpo_terms: 'HP:0001250;HP:0000707' },
    };
    render(
      <MemoryRouter initialEntries={['/families/F1/structural-variants?project_id=P1&type=DEL']}>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[]}
          variants={[withHpo]}
          tags={[]}
        />
      </MemoryRouter>,
    );
    const link = screen.getByRole('link', { name: /HP:0001250/ });
    const params = new URLSearchParams((link.getAttribute('href') ?? '').split('?')[1]);
    expect(params.getAll('term')).toEqual(['HP:0001250', 'HP:0000707']);
    expect(params.get('family_id')).toBe('F1');
    expect(params.get('from')).toBe('/families/F1/structural-variants?project_id=P1&type=DEL');
  });
});

describe('StructuralVariantCards per caller', () => {
  it('shows one card per caller when two callers share an SV id', () => {
    const errors = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    render(
      <MemoryRouter>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[]}
          variants={[
            { ...variant, source: 'sniffles' },
            { ...variant, source: 'spectre' },
          ]}
          tags={[]}
        />
      </MemoryRouter>,
    );
    expect(screen.getAllByRole('article')).toHaveLength(2);
    expect(errors.mock.calls.flat().join(' ')).not.toMatch(/same key/);
    errors.mockRestore();
  });
});

// The anatomy every variant card shares (FindingCard): the gene to its gene page and its
// disease to OMIM in the heading, the variant then the genotypes, the links in one order.
describe('StructuralVariantCards anatomy', () => {
  const annotated: StructuralVariant = {
    ...variant,
    gene: 'ITGB6,RBMS1',
    genotypes: [{ sample: 'PROBAND', gt: '0/1', qual: 40, read_support: 12, filter: 'PASS' }],
    annotation_extra: { omim_phenotype: 'Amelogenesis imperfecta' },
  } as StructuralVariant;

  it('reads as every variant card does', () => {
    const { container } = render(
      <MemoryRouter>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[{ sample_id: 'PROBAND', role: 'proband', sex: 'female', affected: true } as never]}
          variants={[annotated]}
          tags={[]}
        />
      </MemoryRouter>,
    );
    const card = container.querySelector('article') as HTMLElement;
    expect(card).toHaveClass('variant-card', 'finding-card');
    const heading = card.querySelector('.gene-disease-heading') as HTMLElement;
    expect(within(heading).getByRole('link', { name: 'ITGB6' })).toHaveAttribute(
      'href',
      '/genes?gene=ITGB6&family_id=F1&project_id=P1',
    );
    expect(within(heading).getByRole('link', { name: /amelogenesis imperfecta on omim/i })).toBeInTheDocument();
    // The other genes the SV spans are named in a tooltip.
    const more = within(heading).getByText('+1 gene');
    fireEvent.focus(more);
    expect(screen.getByRole('tooltip')).toHaveTextContent('ITGB6, RBMS1');
    expect(Array.from(card.querySelectorAll('.variant-card-col-title')).map((title) => title.textContent)).toEqual([
      'Variant',
      'Genotypes',
      'Frequencies',
      'Disease & predictors',
    ]);
    const line = card.querySelector('.variant-card-gtline') as HTMLElement;
    expect(line).toHaveClass('variant-card-gtline--warn', 'is-proband');
    expect(line).toHaveTextContent('♀PROBANDprobandHet4012PASS');
    const nav = card.querySelector('.variant-card-footer .variant-card-nav') as HTMLElement;
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'IGV',
      'Chromosome view',
      'OMIM',
    ]);
    expect(within(nav).getByRole('link', { name: 'OMIM' })).toHaveAttribute(
      'href',
      'https://www.omim.org/search?search=ITGB6',
    );
  });
});

describe('StructuralVariantCards excluded variant', () => {
  it('folds to its heading and unfolds on request', () => {
    const excluded = { ...variant, review: { tags: ['excluded'] } } as StructuralVariant;
    const { container } = render(
      <MemoryRouter>
        <StructuralVariantCards
          familyId="F1"
          linkSearch=""
          members={[]}
          variants={[excluded]}
          tags={[]}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );
    const card = container.querySelector('article') as HTMLElement;
    expect(within(card).getByRole('link', { name: 'ITGB6' })).toBeInTheDocument();
    expect(card.querySelector('.variant-card-cols')).toBeNull();
    fireEvent.click(within(card).getByRole('button', { name: /show the excluded card of ITGB6/i }));
    expect(card.querySelector('.variant-card-cols')).not.toBeNull();
  });
});

// The owner's decision of 2026-11-10: an SV card shows its review's class by the one rule of the
// family page (reviewClassChips), as a small-variant card does: once, in its heading, and in its
// tag row only as its own class tag. Text "VUS" beside the tag of class 4 shows *VUS - class 3*
// alone, not *Likely pathogenic - class 4* beside it.
describe('StructuralVariantCards review class', () => {
  const renderReview = (fields: Partial<SmallVariantReview>) =>
    render(
      <MemoryRouter>
        <StructuralVariantCards
          familyId="F1"
          projectId="P1"
          linkSearch=""
          members={[]}
          variants={[{ ...variant, review: review({ variant_id: variant._id, ...fields }) }]}
          tags={BUILT_IN_REVIEW_TAGS}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );

  const classLabels = Object.values(ACMG_CLASSIFICATION_LABELS).map((label) => label.toLowerCase());
  // The classes a card shows: its heading's and its tag row's chips that read as a class, or as
  // the text shown.
  const classesOn = (card: HTMLElement, shown: string) =>
    Array.from(card.querySelectorAll('.variant-card-headtags .variant-card-chip, .variant-card-tag-row .variant-card-chip'))
      .map((chip) => chip.textContent ?? '')
      .filter((text) => classLabels.includes(text.toLowerCase()) || (shown !== '' && text === shown));

  it.each(reviewClassCases.cases)('shows the class the rule gives, in its heading, once: $why', ({ classification, tags, shown }) => {
    const { container } = renderReview({ classification, tags });
    const card = container.querySelector('article') as HTMLElement;
    // Once in the heading; among the tags only as the class shown's own tag.
    expect([...new Set(classesOn(card, shown))]).toEqual(shown ? [shown] : []);
    const heading = card.querySelector('.variant-card-headtags');
    if (shown) {
      expect(within(heading as HTMLElement).getByText(shown)).toBeInTheDocument();
    } else {
      expect(heading).toBeNull();
    }
  });

  it('keeps a VUS tier and Secondary finding as tags beside the class word', () => {
    const { container } = renderReview({ classification: 'VUS', tags: ['acmg_vus_warm', 'secondary_finding'] });
    const card = container.querySelector('article') as HTMLElement;
    const tagRow = card.querySelector('.variant-card-tag-row') as HTMLElement;
    expect(within(tagRow).getByText('VUS — warm')).toBeInTheDocument();
    expect(within(tagRow).getByText('Secondary finding')).toBeInTheDocument();
    expect(classesOn(card, 'VUS - class 3')).toEqual(['VUS - class 3']);
  });

  it('shows no class tag but the class shown’s own in its tag row', () => {
    const { container } = renderReview({ classification: 'VUS', tags: ['acmg_class_4'] });
    const card = container.querySelector('article') as HTMLElement;
    expect(card.querySelector('.variant-card-tag-row')).toBeNull();
    expect(classesOn(card, 'VUS - class 3')).toEqual(['VUS - class 3']);
  });
});
