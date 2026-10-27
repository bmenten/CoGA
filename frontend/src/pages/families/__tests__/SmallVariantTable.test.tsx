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

  // ENG-10: the table showed the classification as typed ("likely pathogenic"), where the
  // SV pages show it normalised.
  it('shows a review classification as the SV pages show it, not as typed', () => {
    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            {
              _id: 'classified',
              chr: '1',
              start: 123,
              end: 123,
              type: 'SNV',
              gene: 'GENE1',
              ref: 'G',
              alt: 'A',
              impact: 'MODERATE',
              effect: 'missense_variant',
              genotypes: [],
              review: { variant_id: 'classified', classification: ' likely pathogenic ', tags: [], tag_metadata: {} },
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

    expect(screen.getByText('Likely Pathogenic - class 4')).toHaveClass('table-chip--critical');
    expect(screen.queryByText(/likely pathogenic/)).not.toBeInTheDocument();
  });

  it('names under the effect the transcript the filters held on, when not the row’s own', () => {
    // CLIN-51: the row's impact and effect are its MANE transcript's, a synonymous change;
    // HIGH or MODERATE impact kept it as a missense change on a second transcript.
    const row = {
      _id: '20-63000301-C-T',
      chr: '20',
      start: 63000301,
      end: 63000301,
      type: 'SNV',
      gene: 'SYN51C',
      ref: 'C',
      alt: 'T',
      impact: 'LOW',
      effect: 'synonymous_variant',
      genotypes: [],
    };
    const matched = {
      gene: 'SYN51C',
      transcript_id: 'ENST_SYN51C_2',
      impact: 'MODERATE',
      effect: 'missense_variant',
      hgvsc: null,
      hgvsp: null,
      lof: null,
      cadd_phred: null,
      revel: null,
      spliceai_max: null,
      sift: 'tolerated(0.5)',
      polyphen: null,
    };
    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            { ...row, filter_transcript: matched },
            { ...row, _id: '20-63000401-C-T', start: 63000401, end: 63000401, filter_transcript: null },
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

    const [note] = screen.getAllByText(/^Filters met on/);
    expect(note.closest('p')).toHaveTextContent(
      'Filters met on ENST_SYN51C_2: missense variant · SIFT tolerated (0.5)',
    );
    // In the effect cell, beside the row's own effect, which stays.
    expect(note.closest('td')).toHaveTextContent(/^synonymous_variant/);
    // The row whose own transcript met the filters says nothing.
    expect(screen.getAllByText(/^Filters met on/)).toHaveLength(1);
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

  it('writes the NIPT VAFs as the NIPT pages do: a percentage, a dash when missing', () => {
    const niptVariant = (id: string, observedVaf: number | null) => ({
      _id: id,
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
      nipt: {
        category: 7,
        category_label: 'Paternal, transmitted',
        maternal_state: 'hom_ref',
        fetal_inheritance: 'paternal',
        expected_vaf: 0.05,
        observed_vaf: observedVaf,
        confidence: 0.873,
        flags: [],
      },
    });

    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[niptVariant('seen', 0.123), niptVariant('unseen', null)]}
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

    expect(screen.getByRole('columnheader', { name: 'NIPT' })).toBeInTheDocument();
    expect(screen.getByText('VAF 12.3% / 5.0% · conf 0.87')).toBeInTheDocument();
    expect(screen.getByText('VAF — / 5.0% · conf 0.87')).toBeInTheDocument();
  });
});

describe('SmallVariantTable SV second hit', () => {
  const renderWithHit = (props: { familyId?: string; projectId?: string }) =>
    render(
      <MemoryRouter>
        <SmallVariantTable
          variants={[
            {
              _id: 'trnt1-var',
              chr: '3',
              start: 3129108,
              end: 3129108,
              type: 'SNV',
              gene: 'TRNT1',
              ref: 'C',
              alt: 'T',
              impact: 'MODERATE',
              effect: 'missense_variant',
              genotypes: [],
              sv_second_hit: {
                sv_count: 1,
                sv_types: ['DEL'],
                affected_zygosity: 'het',
                has_deletion: true,
                phase: 'trans',
                deletion_unmasked: true,
              },
            },
          ]}
          members={[]}
          locationSearch=""
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
          {...props}
        />
      </MemoryRouter>,
    );

  it('links the badge to the family SV list filtered to the gene', () => {
    renderWithHit({ familyId: 'F1', projectId: 'P1' });
    expect(screen.getByRole('link', { name: /also hit by a deletion/i })).toHaveAttribute(
      'href',
      '/families/F1/structural-variants?gene=TRNT1&project_id=P1',
    );
  });

  it('still shows the badge when there is no family to link to', () => {
    renderWithHit({});
    expect(screen.queryByRole('link', { name: /also hit by a deletion/i })).not.toBeInTheDocument();
    expect(screen.getByText(/SV: DEL/)).toBeInTheDocument();
  });
});
