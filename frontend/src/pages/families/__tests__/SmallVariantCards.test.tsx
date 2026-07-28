import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { describe, expect, it, vi } from 'vitest';

import SmallVariantCards from '../SmallVariantCards';

describe('SmallVariantCards', () => {
  it('opens a transcript popup with all transcript effects from the transcript field', async () => {
    render(
      <MemoryRouter>
        <SmallVariantCards
          variants={[
            {
              _id: 'brca2-var',
              chr: '13',
              start: 32316461,
              end: 32316461,
              type: 'SNV',
              source: 'clair3',
              gene: 'BRCA2',
              gene_id: 'ENSG00000139618',
              transcript_id: 'NM_000059.4',
              transcript_biotype: 'protein_coding',
              impact: 'MODERATE',
              effect: 'missense_variant',
              hgvsc: 'NM_000059.4:c.7007G>A',
              hgvsp: 'NP_000050.3:p.Arg2336His',
              ref: 'G',
              alt: 'A',
              mane_select: true,
              exon: '13/27',
              transcripts: [
                {
                  gene: 'BRCA2',
                  gene_id: 'ENSG00000139618',
                  transcript_id: 'NM_000059.4',
                  transcript_source: 'RefSeq',
                  transcript_biotype: 'protein_coding',
                  impact: 'MODERATE',
                  effect: 'missense_variant',
                  hgvsc: 'NM_000059.4:c.7007G>A',
                  hgvsp: 'NP_000050.3:p.Arg2336His',
                  exon: '13/27',
                  mane_select: true,
                  primary: true,
                },
                {
                  gene: 'BRCA2',
                  gene_id: 'ENSG00000139618',
                  transcript_id: 'ENST00000380152.8',
                  transcript_source: 'Ensembl',
                  transcript_biotype: 'protein_coding',
                  impact: 'MODERATE',
                  effect: 'missense_variant',
                  hgvsc: 'ENST00000380152.8:c.7007G>A',
                  hgvsp: 'ENSP00000369497.3:p.Arg2336His',
                  exon: '13/27',
                  canonical: true,
                },
              ],
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

    await userEvent.click(screen.getByRole('button', { name: /NM_000059\.4.*2 transcripts/i }));

    const dialog = screen.getByRole('dialog', { name: /BRCA2/i });
    expect(within(dialog).getByText('Transcripts')).toBeInTheDocument();
    expect(within(dialog).getByText('chr13:32,316,461-32,316,461 · G → A')).toBeInTheDocument();
    expect(within(dialog).getByText('NM_000059.4:c.7007G>A')).toBeInTheDocument();
    expect(within(dialog).getAllByText('NP_000050.3:p.Arg2336His')).toHaveLength(2);
    expect(within(dialog).getByText('ENST00000380152.8')).toBeInTheDocument();
    expect(within(dialog).getByText('Ensembl · protein_coding')).toBeInTheDocument();
    expect(within(dialog).getByText('MANE Select')).toBeInTheDocument();
    expect(within(dialog).getByText('Canonical')).toBeInTheDocument();
    expect(within(dialog).getAllByText('Exon 13/27')).toHaveLength(2);
  });

  it('shows the priority breakdown on a prioritized variant card', () => {
    render(
      <MemoryRouter>
        <SmallVariantCards
          variants={[
            {
              _id: 'antxr2-var',
              chr: '4',
              start: 80097499,
              end: 80097499,
              type: 'SNV',
              gene: 'ANTXR2',
              impact: 'HIGH',
              effect: 'splice_donor_variant',
              ref: 'C',
              alt: 'T',
              genotypes: [],
              priority: {
                combined_score: 0.46,
                variant_score: 0.85,
                pathogenicity_score: 0.85,
                frequency_score: 1,
                segregation_weight: 1,
                phenotype_score: 0.07,
                segregation_modes: ['homozygous_recessive'],
                phenotype_gene: 'ANTXR2',
                phenotype_matches: [
                  { hpo_id: 'HP:0000851', label: 'Congenital hypothyroidism' },
                ],
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

    expect(screen.getByText(/Priority 0.46/)).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText(/Inheritance: homozygous recessive/i)).toBeInTheDocument();
    expect(screen.getByText(/Congenital hypothyroidism/)).toBeInTheDocument();
  });
});
