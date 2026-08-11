import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmallVariantCards from '../SmallVariantCards';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

// The transcript modal reads CCDS and RefSeq from the imported gene annotation, since
// the VCF annotation carries neither.
const mockGeneProfile = (transcripts: unknown[]) => {
  (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
    url === '/genes/profile'
      ? Promise.resolve({ data: { transcripts } })
      : Promise.resolve({ data: {} }),
  );
};

const renderCards = (ui: React.ReactElement) =>
  render(
    <QueryClientProvider client={createTestQueryClient()}>
      <MemoryRouter>{ui}</MemoryRouter>
    </QueryClientProvider>,
  );

describe('SmallVariantCards', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockGeneProfile([]);
  });

  it('opens a transcript popup with all transcript effects from the transcript field', async () => {
    renderCards(
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
        />,
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

  it('badges CCDS and names RefSeq accessions from the gene annotation', async () => {
    // The VCF annotation has no CCDS or RefSeq; the imported GENCODE rows do, keyed by
    // the transcript the variant names — versions differ, so matching is on the stem.
    mockGeneProfile([
      {
        transcript_id: 'ENST00000380152.8',
        ccds_id: 'CCDS9344.1',
        refseq_accessions: ['NM_000059.4'],
      },
    ]);

    renderCards(
        <SmallVariantCards
          variants={[
            {
              _id: 'brca2-var',
              chr: '13',
              start: 32316461,
              end: 32316461,
              type: 'SNV',
              gene: 'BRCA2',
              ref: 'G',
              alt: 'A',
              impact: 'MODERATE',
              effect: 'missense_variant',
              transcript_id: 'ENST00000380152',
              transcript_biotype: 'protein_coding',
              canonical: true,
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
        />,
    );

    await userEvent.click(screen.getByRole('button', { name: /ENST00000380152/i }));
    const dialog = await screen.findByRole('dialog', { name: /BRCA2/i });

    // Matched despite the VCF naming the unversioned transcript.
    expect(await within(dialog).findByText('CCDS')).toBeInTheDocument();
    expect(within(dialog).getByText('NM_000059.4')).toBeInTheDocument();
  });

  it('shows the priority breakdown on a prioritized variant card', () => {
    renderCards(
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
        />,
    );

    expect(screen.getByText(/Priority 0.46/)).toBeInTheDocument();
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText(/Inheritance: homozygous recessive/i)).toBeInTheDocument();
    expect(screen.getByText(/Congenital hypothyroidism/)).toBeInTheDocument();
  });
});
