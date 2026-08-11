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

  it('lists Monarch and OMIM associations when the card detail is expanded', async () => {
    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string) =>
      url === '/genes/profile'
        ? Promise.resolve({
            data: {
              transcripts: [],
              monarch_associations: [
                {
                  mondo_id: 'MONDO:0700269',
                  disease_label: 'BRCA2-related cancer predisposition',
                  causal: true,
                  sources: ['clingen'],
                  monarch_url: 'https://monarchinitiative.org/MONDO:0700269',
                },
              ],
              extra: {
                omim_diseases: [
                  { label: 'Breast cancer', omim_id: '114480', href: 'https://www.omim.org/entry/114480' },
                ],
              },
            },
          })
        : Promise.resolve({ data: {} }),
    );

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

    // Collapsed: the section is not rendered, so nothing is fetched for it.
    expect(screen.queryByText(/Gene–disease associations/)).not.toBeInTheDocument();

    await userEvent.click(screen.getByText('More annotations & detail'));

    expect(await screen.findByText(/Gene–disease associations/)).toBeInTheDocument();
    expect(
      await screen.findByRole('link', { name: 'BRCA2-related cancer predisposition' }),
    ).toHaveAttribute('href', 'https://monarchinitiative.org/MONDO:0700269');
    // Causal is stated, because it changes how the association reads.
    expect(screen.getByText('causal')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Breast cancer' })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/114480',
    );
  });

  it('keeps the card the primary view and the disclosure strictly extra', async () => {
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
              rsid: 'rs80359550',
              hgvsc: 'ENST00000380152.8:c.7007G>A',
              hgvsp: 'ENSP00000369497.3:p.Arg2336His',
              transcript_id: 'ENST00000380152.8',
              impact: 'MODERATE',
              effect: 'missense_variant',
              gene_pli: 0.99,
              polyphen: 'probably_damaging',
              spliceai_max: 0.12,
              // AlphaMissense is a first-class field, and the annotation repeats it under
              // its own key — both shapes are what the backend really sends.
              alpha_missense_pathogenicity: 0.81,
              alpha_missense_class: 'likely_pathogenic',
              annotation_extra: {
                alpha_missense_pathogenicity: 0.81,
                alpha_missense_class: 'likely_pathogenic',
                some_other_field: 'keep me',
              },
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

    // The card carries the essentials, including the AlphaMissense score under In silico.
    const silico = screen.getByText('In silico').closest('section') as HTMLElement;
    expect(within(silico).getByText('AlphaMissense')).toBeInTheDocument();
    expect(within(silico).getByText(/0\.810 · likely pathogenic/)).toBeInTheDocument();
    expect(within(silico).getByText('pLI')).toBeInTheDocument();

    await userEvent.click(screen.getByText('More annotations & detail'));

    // Everything the card already states appears exactly once — the disclosure is extra
    // detail, not a second copy of the card.
    for (const label of [
      'HGVS.g',
      'HGVS.c',
      'HGVS.p',
      'Alleles',
      'dbSNP',
      'AlphaMissense',
      'pLI',
      'PolyPhen',
      'SpliceAI',
    ]) {
      expect(screen.getAllByText(label)).toHaveLength(1);
    }
    // Genuinely extra annotation still shows.
    expect(screen.getByText(/some other field/i)).toBeInTheDocument();
  });

  it('heads the card with HGVS.g instead of a locus and links dbSNP out', async () => {
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
              rsid: 'rs80359550',
              hgvsc: 'ENST00000380152.8:c.7007G>A',
              transcript_id: 'ENST00000380152.8',
              impact: 'MODERATE',
              effect: 'missense_variant',
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

    expect(screen.getByText('chr13:g.32316461G>A')).toBeInTheDocument();
    // The headline no longer repeats the position as a locus range.
    expect(screen.queryByText(/32,316,461/)).not.toBeInTheDocument();

    const dbSnp = screen.getByRole('link', { name: 'rs80359550' });
    expect(dbSnp).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/snp/rs80359550');
  });

  it('folds the disclosure into combined context and annotation sections', async () => {
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
              gene_id: 'ENSG00000139618',
              ref: 'G',
              alt: 'A',
              transcript_biotype: 'protein_coding',
              lof_filter: 'END_TRUNC',
              impact: 'MODERATE',
              effect: 'missense_variant',
              annotation_extra: { some_other_field: 'keep me' },
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

    await userEvent.click(screen.getByText('More annotations & detail'));

    // Two combined sections replace the four that used to split these rows.
    for (const gone of ['Variant summary', 'Transcript context', 'All scores & constraint', 'Additional annotations']) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    const context = screen
      .getByText('Variant & transcript context')
      .closest('.variant-card-section') as HTMLElement;
    expect(within(context).getByText('Type / source')).toBeInTheDocument();
    expect(within(context).getByText('Biotype')).toBeInTheDocument();

    const annotations = screen
      .getByText('Other scores & annotations')
      .closest('.variant-card-section') as HTMLElement;
    expect(within(annotations).getByText('LoF filter')).toBeInTheDocument();
    expect(within(annotations).getByText(/some other field/i)).toBeInTheDocument();
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
