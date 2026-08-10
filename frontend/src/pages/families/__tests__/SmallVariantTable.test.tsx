import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';
import SmallVariantTable from '../SmallVariantTable';

describe('SmallVariantTable', () => {
  it('keeps long allele values constrained while preserving the full value in the tooltip', () => {
    const longAlt = `A${'C'.repeat(120)}T`;

    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            {
              _id: 'long-indel',
              chr: '1',
              start: 123,
              end: 123,
              type: 'INDEL',
              gene: 'GENE1',
              ref: 'G',
              alt: longAlt,
              impact: 'MODERATE',
              effect: 'frameshift_variant',
              genotypes: [],
            },
          ]}
          members={[]}
          familyId="F1"
          projectId="P1"
          locationSearch=""
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );

    const allele = screen.getByTitle(longAlt);
    expect(allele).toHaveClass('variant-allele-value');
    expect(allele.textContent?.length).toBeLessThan(longAlt.length);
    expect(allele.closest('td')).toHaveClass('variant-allele-cell');
    expect(allele.closest('table')).toHaveClass('small-variant-table');
  });

  it('shows a Score column and ranks prioritized variants highest-first', () => {
    const mkVariant = (id: string, gene: string, combined: number) => ({
      _id: id,
      chr: '1',
      start: 100,
      end: 100,
      type: 'SNV',
      gene,
      ref: 'A',
      alt: 'G',
      impact: 'HIGH',
      effect: 'stop_gained',
      genotypes: [],
      priority: {
        combined_score: combined,
        variant_score: combined,
        pathogenicity_score: 0.85,
        frequency_score: 1,
        segregation_weight: 1,
        phenotype_score: combined > 0.5 ? 0.9 : null,
        segregation_modes: ['de_novo'],
        phenotype_gene: gene,
        phenotype_matches: combined > 0.5 ? [{ hpo_id: 'HP:0001250', label: 'Seizure' }] : [],
        rank: null,
      },
    });

    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[mkVariant('low', 'LOWG', 0.3), mkVariant('high', 'TOPG', 0.92)]}
          members={[]}
          familyId="F1"
          projectId="P1"
          locationSearch=""
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText(/^Score/)).toBeInTheDocument();
    // Higher combined score sorts to the top row.
    const geneLinks = screen.getAllByRole('link').filter((el) => /TOPG|LOWG/.test(el.textContent || ''));
    expect(geneLinks[0]).toHaveTextContent('TOPG');
    expect(screen.getByText('0.92')).toBeInTheDocument();
  });

  it('reveals the priority breakdown in a popover on score hover', async () => {
    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            {
              _id: 'top',
              chr: '1',
              start: 100,
              end: 100,
              type: 'SNV',
              gene: 'TOPG',
              ref: 'A',
              alt: 'G',
              impact: 'HIGH',
              effect: 'stop_gained',
              genotypes: [],
              priority: {
                combined_score: 0.92,
                variant_score: 0.92,
                pathogenicity_score: 0.85,
                frequency_score: 1,
                segregation_weight: 1,
                phenotype_score: 0.9,
                segregation_modes: ['de_novo'],
                phenotype_gene: 'TOPG',
                phenotype_matches: [{ hpo_id: 'HP:0001250', label: 'Seizure' }],
                rank: 1,
              },
            },
          ]}
          members={[]}
          familyId="F1"
          projectId="P1"
          locationSearch=""
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );

    // No breakdown until hovered.
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument();
    await userEvent.hover(screen.getByRole('button', { name: /priority score 0.92/i }));
    const tooltip = await screen.findByRole('tooltip');
    expect(tooltip).toHaveTextContent(/Priority 0.92/);
    expect(tooltip).toHaveTextContent(/Inheritance: de novo/i);
    expect(tooltip).toHaveTextContent(/Seizure/);
  });

  it('opens the IGV and chromosome-view links in a new tab so the filtered list survives', () => {
    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            {
              _id: 'nav',
              chr: '1',
              start: 100,
              end: 100,
              type: 'SNV',
              gene: 'GENE1',
              ref: 'A',
              alt: 'G',
              impact: 'HIGH',
              effect: 'stop_gained',
              genotypes: [],
            },
          ]}
          members={[]}
          familyId="F1"
          projectId="P1"
          locationSearch="?gene=GENE1"
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
        />
      </MemoryRouter>,
    );

    const igv = screen.getByRole('link', { name: /open igv at chr1:100-100 \(opens in a new tab\)/i });
    expect(igv).toHaveAttribute('target', '_blank');
    expect(igv).toHaveAttribute('rel', 'noopener noreferrer');
    expect(igv).toHaveAttribute('href', expect.stringContaining('/families/F1/igv'));

    const view = screen.getByRole('link', {
      name: /open chromosome view around 1:100-100 \(opens in a new tab\)/i,
    });
    expect(view).toHaveAttribute('target', '_blank');
    expect(view).toHaveAttribute('rel', 'noopener noreferrer');
    expect(view).toHaveAttribute('href', expect.stringContaining('/families/F1/chromosome/1'));
  });
});
