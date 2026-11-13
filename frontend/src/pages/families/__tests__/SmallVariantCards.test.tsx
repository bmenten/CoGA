import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmallVariantCards from '../SmallVariantCards';
import { ACMG_CLASSIFICATION_LABELS, type SmallVariant, type SmallVariantReview } from '../smallVariantSearch';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { BUILT_IN_REVIEW_TAGS } from '../../../test/tagDefinitions';
import { genotype, review, smallVariant, svSecondHit, transcript } from '../../../test/variants';
import reviewClassCases from './fixtures/review-class-cases.json';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

type DiseaseLinks = { monarch?: unknown[]; omim?: unknown[] };
type ApiConfig = { params?: { symbol?: string[] } };

// GET /genes/disease-links answers one entry per gene asked, with empty lists for a gene
// without a link, as the server does.
const diseaseLinksAnswer = (config: ApiConfig | undefined, links: Record<string, DiseaseLinks> = {}) =>
  Promise.resolve({
    data: (config?.params?.symbol ?? []).map((symbol) => ({
      symbol,
      monarch: links[symbol]?.monarch ?? [],
      omim: links[symbol]?.omim ?? [],
    })),
  });

// The transcript modal reads CCDS and RefSeq from the imported gene annotation, since
// the VCF annotation carries neither.
const mockGeneProfile = (transcripts: unknown[]) => {
  (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string, config?: ApiConfig) =>
    url === '/genes/profile'
      ? Promise.resolve({ data: { transcripts } })
      : url === '/genes/disease-links'
        ? diseaseLinksAnswer(config)
        : Promise.resolve({ data: {} }),
  );
};

const mockDiseaseLinks = (links: Record<string, DiseaseLinks>) => {
  (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string, config?: ApiConfig) =>
    url === '/genes/disease-links' ? diseaseLinksAnswer(config, links) : Promise.resolve({ data: {} }),
  );
};

const monarchLink = (label: string, causal: boolean, sources: string[] = ['omim'], mondo = label) => ({
  mondo_id: `MONDO:${mondo}`,
  disease_label: label,
  causal,
  sources,
  monarch_url: `https://monarchinitiative.org/MONDO:${mondo}`,
});

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
            smallVariant({
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
              // The primary transcript is the variant's own fields; the list holds the others.
              transcripts: [
                transcript({
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
                }),
              ],
              genotypes: [],
            }),
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
            smallVariant({
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
            }),
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

    // Matched despite the VCF naming the unversioned transcript, and the annotation
    // version the cross-references come from is named.
    expect(await within(dialog).findByText('CCDS')).toBeInTheDocument();
    expect(within(dialog).getByText('NM_000059.4')).toBeInTheDocument();
    expect(
      within(dialog).getByText(/CCDS\/RefSeq from ENST00000380152\.8 in the gene annotation/),
    ).toBeInTheDocument();
  });

  const renderBrca2WithTranscript = (transcriptId: string) =>
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
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
            transcript_id: transcriptId,
            transcript_biotype: 'protein_coding',
            canonical: true,
            genotypes: [],
          }),
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

  // OMIM and DECIPHER sit in the card's footer with IGV and the chromosome view, as the
  // card's other ways out, with their links unchanged.
  it('lists OMIM and DECIPHER with IGV and the chromosome view in the card footer', () => {
    const { container } = renderBrca2WithTranscript('ENST00000380152.8');
    const nav = container.querySelector('.variant-card-nav') as HTMLElement;
    const names = within(nav)
      .getAllByRole('link')
      .map((link) => link.textContent);
    expect(names).toEqual(['IGV', 'Chromosome view', 'OMIM', 'DECIPHER']);
    const omim = within(nav).getByRole('link', { name: 'OMIM' });
    expect(omim).toHaveAttribute('href', 'https://www.omim.org/search?search=BRCA2');
    expect(omim).toHaveAttribute('target', '_blank');
    expect(omim).toHaveAttribute('rel', 'noreferrer');
    const decipher = within(nav).getByRole('link', { name: 'DECIPHER' });
    expect(decipher).toHaveAttribute('href', 'https://www.deciphergenomics.org/gene/BRCA2');
    expect(decipher).toHaveAttribute('target', '_blank');
    // Only once on the card: not under the in-silico predictions.
    expect(screen.getAllByRole('link', { name: 'OMIM' })).toHaveLength(1);
    expect(screen.getAllByRole('link', { name: 'DECIPHER' })).toHaveLength(1);
  });

  // The genotype table's numbers are right-aligned under right-aligned headings.
  it('right-aligns the depth, allele depth and allele fraction headings', () => {
    const { container } = renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'gt-var',
            type: 'SNV',
            chr: '13',
            start: 32316461,
            end: 32316461,
            ref: 'G',
            alt: 'A',
            gene: 'BRCA2',
            genotypes: [genotype({ sample: 'P1', gt: '0/1', dp: 30, ad: [15, 15] })],
          }),
        ]}
        members={[{ sample_id: 'P1', role: 'proband', affected: true, sex: 'female' }]}
        familyId="F1"
        projectId="P1"
        locationSearch=""
        tags={[]}
        onEditReview={vi.fn()}
        onAcmgClassify={vi.fn()}
        onToggleReviewTag={vi.fn(async () => undefined)}
      />,
    );
    const head = container.querySelector('.variant-card-gthead') as HTMLElement;
    expect(
      Array.from(head.querySelectorAll('.variant-card-gthead-num')).map((cell) => cell.textContent),
    ).toEqual(['DP', 'AD', 'AF']);
    const line = container.querySelector('.variant-card-gtline') as HTMLElement;
    expect(line).toHaveTextContent('P1');
    expect(within(line).getByText('0/1')).toBeInTheDocument();
    expect(within(line).getByText('15,15')).toBeInTheDocument();
    expect(within(line).getByText('0.50')).toBeInTheDocument();
  });

  it('shows CCDS and RefSeq without a note for the same transcript version', async () => {
    mockGeneProfile([
      { transcript_id: 'ENST00000380152.8', ccds_id: 'CCDS9344.1', refseq_accessions: ['NM_000059.4'] },
    ]);
    renderBrca2WithTranscript('ENST00000380152.8');
    await userEvent.click(screen.getByRole('button', { name: /ENST00000380152\.8/i }));
    const dialog = await screen.findByRole('dialog', { name: /BRCA2/i });

    expect(await within(dialog).findByText('CCDS')).toBeInTheDocument();
    expect(within(dialog).getByText('NM_000059.4')).toBeInTheDocument();
    expect(within(dialog).queryByText(/CCDS\/RefSeq (from|not shown)/)).not.toBeInTheDocument();
  });

  it('withholds CCDS and RefSeq when the variant names another transcript version', async () => {
    // The annotation's .8 may not share a CDS with the .7 the variant was annotated on.
    mockGeneProfile([
      { transcript_id: 'ENST00000380152.8', ccds_id: 'CCDS9344.1', refseq_accessions: ['NM_000059.4'] },
    ]);
    renderBrca2WithTranscript('ENST00000380152.7');
    await userEvent.click(screen.getByRole('button', { name: /ENST00000380152\.7/i }));
    const dialog = await screen.findByRole('dialog', { name: /BRCA2/i });

    expect(
      await within(dialog).findByText('CCDS/RefSeq not shown: the gene annotation has ENST00000380152.8'),
    ).toBeInTheDocument();
    expect(within(dialog).queryByText('CCDS')).not.toBeInTheDocument();
    expect(within(dialog).queryByText('NM_000059.4')).not.toBeInTheDocument();
  });

  const renderGeneCards = (genes: Array<string | null>) =>
    renderCards(
      <SmallVariantCards
        variants={genes.map((gene, index) =>
          smallVariant({
            _id: `var-${index}`,
            chr: '13',
            start: 32316461 + index,
            end: 32316461 + index,
            type: 'SNV',
            gene,
            ref: 'G',
            alt: 'A',
            genotypes: [],
          }),
        )}
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

  const diseaseLine = (container: HTMLElement, index = 0) =>
    container.querySelectorAll<HTMLElement>('.variant-card-diseases')[index];

  it('names the diseases the gene is linked to under the heading, without opening the detail', async () => {
    mockDiseaseLinks({
      BRCA2: {
        monarch: [
          monarchLink('BRCA2-related cancer predisposition', true, ['clingen'], '0700269'),
          monarchLink('Fanconi anemia complementation group D1', true, ['omim', 'orphanet'], '0011366'),
          monarchLink('Wilms tumor 1', true, ['omim'], '0006058'),
          monarchLink('breast-ovarian cancer, familial, susceptibility to, 2', true, ['clingen', 'omim'], '0011450'),
          monarchLink('medulloblastoma', false, ['omim'], '0007959'),
        ],
        omim: [{ label: 'Breast cancer', omim_id: '114480', href: 'https://www.omim.org/entry/114480' }],
      },
    });
    const { container } = renderGeneCards(['BRCA2']);

    const line = await within(container).findByText(/Fanconi anemia complementation group D1/);
    // Causal links first, the better-supported ones first; the line names three and counts
    // the rest of the same kind.
    expect(line).toHaveTextContent(
      'Fanconi anemia complementation group D1 · breast-ovarian cancer, familial, susceptibility to, 2 · BRCA2-related cancer predisposition',
    );
    expect(diseaseLine(container)).toHaveTextContent('Monarch, causal');
    expect(within(diseaseLine(container)).getByRole('button', { name: '+1 more' })).toBeInTheDocument();
    // The full list stays in the detail, which is still closed.
    expect(screen.queryByText(/Gene–disease associations/)).not.toBeInTheDocument();
    expect(screen.queryByText('medulloblastoma')).not.toBeInTheDocument();
  });

  it('opens the detail with every link from the summary line', async () => {
    mockDiseaseLinks({
      BRCA2: {
        monarch: ['A', 'B', 'C', 'D'].map((name) => monarchLink(`Disease ${name}`, true)),
      },
    });
    const { container } = renderGeneCards(['BRCA2']);

    await userEvent.click(await within(diseaseLine(container)).findByRole('button', { name: '+1 more' }));

    expect(await screen.findByText(/Gene–disease associations/)).toBeInTheDocument();
    expect(container.querySelector('details.variant-card-more')).toHaveAttribute('open');
    expect(screen.getByRole('link', { name: 'Disease D' })).toBeInTheDocument();
  });

  it('falls back to OMIM, then to associations it marks as not causal', async () => {
    mockDiseaseLinks({
      GENE1: {
        monarch: [monarchLink('associated condition', false)],
        omim: [{ label: 'Some syndrome', omim_id: '100100', href: 'https://www.omim.org/entry/100100' }],
      },
      GENE2: { monarch: [monarchLink('susceptibility condition', false)] },
    });
    const { container } = renderGeneCards(['GENE1', 'GENE2', 'GENE3']);

    await within(container).findByText('Some syndrome');
    expect(diseaseLine(container, 0)).toHaveTextContent('Some syndrome');
    expect(diseaseLine(container, 0)).toHaveTextContent('OMIM');
    expect(diseaseLine(container, 0)).not.toHaveTextContent('associated condition');
    expect(diseaseLine(container, 1)).toHaveTextContent('susceptibility condition');
    expect(diseaseLine(container, 1)).toHaveTextContent('Monarch, associated only, not causal');
    expect(diseaseLine(container, 2)).toHaveTextContent('No gene–disease link cached for GENE3');
  });

  it('looks up the page’s genes in one request', async () => {
    mockDiseaseLinks({});
    const { container } = renderGeneCards(['FBN1', 'BRCA2', 'FBN1', null]);

    await within(container).findAllByText(/No gene–disease link cached/);
    const lookups = (api.get as unknown as ReturnType<typeof vi.fn>).mock.calls.filter(
      ([url]) => url === '/genes/disease-links',
    );
    expect(lookups).toHaveLength(1);
    expect(lookups[0][1]).toMatchObject({ params: { symbol: ['BRCA2', 'FBN1'] } });
    // A variant without a gene has no line.
    expect(container.querySelectorAll('.variant-card-diseases')).toHaveLength(3);
  });

  it('lists Monarch and OMIM associations when the card detail is expanded', async () => {
    mockDiseaseLinks({
      BRCA2: {
        monarch: [monarchLink('BRCA2-related cancer predisposition', true, ['clingen'], '0700269')],
        omim: [{ label: 'Breast cancer', omim_id: '114480', href: 'https://www.omim.org/entry/114480' }],
      },
    });
    renderGeneCards(['BRCA2']);

    // Collapsed: the detail's section is not rendered.
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
            smallVariant({
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
              // As VEP writes it: call and score joined, underscored.
              polyphen: 'probably_damaging(0.998)',
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
            }),
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
    // VEP joins the call to its score; the card spaces them and drops the underscores.
    expect(within(silico).getByText('probably damaging (0.998)')).toBeInTheDocument();

    await userEvent.click(screen.getByText('More annotations & detail'));

    // Everything the card already states appears exactly once — the disclosure is extra
    // detail, not a second copy of the card.
    for (const label of [
      'Genomic change',
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

  // The annotation filters must all hold on one transcript, and a variant kept by SIFT
  // deleterious on its second transcript shows its primary transcript's "tolerated" on the card,
  // with the transcript the filters held on named beside it.
  // The e2e ready case (variant 20-62900601 of test_e2e_in_silico_prediction_filters.py).
  const keptOnItsSecondTranscript: SmallVariant = smallVariant({
    _id: '20-62900601-C-T',
    chr: '20',
    start: 62900601,
    end: 62900601,
    type: 'SNV',
    gene: 'SYN45F',
    ref: 'C',
    alt: 'T',
    transcript_id: 'ENST_SYN45F_1',
    canonical: true,
    impact: 'MODERATE',
    effect: 'missense_variant',
    hgvsc: 'ENST_SYN45F_1:c.100C>T',
    sift: 'tolerated(0.2)',
    polyphen: 'benign(0.1)',
    transcripts: [
      transcript({
        gene: 'SYN45F',
        transcript_id: 'ENST_SYN45F_2',
        transcript_source: 'Ensembl',
        impact: 'MODERATE',
        effect: 'missense_variant',
        hgvsc: 'ENST_SYN45F_2:c.100C>T',
        hgvsp: null,
      }),
    ],
    filter_transcript: {
      alt_allele: null,
      gene: 'SYN45F',
      transcript_id: 'ENST_SYN45F_2',
      impact: 'MODERATE',
      effect: 'missense_variant',
      hgvsc: 'ENST_SYN45F_2:c.100C>T',
      hgvsp: null,
      lof: null,
      cadd_phred: null,
      revel: null,
      spliceai_max: null,
      sift: 'deleterious(0.02)',
      polyphen: 'probably_damaging(0.95)',
    },
    genotypes: [],
  });

  const renderOne = (variant: SmallVariant) =>
    renderCards(
      <SmallVariantCards
        variants={[variant]}
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

  it('names the transcript the filters held on beside the transcript the card describes', async () => {
    renderOne(keptOnItsSecondTranscript);

    const note = screen.getByText(/^Filters met on/).closest('p') as HTMLElement;
    expect(note).toHaveTextContent(
      'Filters met on ENST_SYN45F_2: SIFT deleterious (0.02) · PolyPhen probably damaging (0.95)',
    );
    // It sits under the card's own transcript, in the Variant column.
    const variantColumn = screen.getByText('Variant').closest('section') as HTMLElement;
    expect(variantColumn).toContainElement(note);
    // The card's predictions stay the primary transcript's.
    const silico = screen.getByText('In silico').closest('section') as HTMLElement;
    expect(within(silico).getByText('tolerated (0.2)')).toBeInTheDocument();
    expect(within(silico).getByText('benign (0.1)')).toBeInTheDocument();

    // The transcript list marks it too, apart from the one shown on the card.
    await userEvent.click(screen.getByRole('button', { name: /ENST_SYN45F_1.*2 transcripts/i }));
    const dialog = screen.getByRole('dialog', { name: /SYN45F/i });
    const matchedRow = within(dialog).getByText('ENST_SYN45F_2').closest('tr') as HTMLElement;
    expect(within(matchedRow).getByText('Filters met')).toBeInTheDocument();
    expect(within(matchedRow).queryByText('Shown on card')).not.toBeInTheDocument();
    const primaryRow = within(dialog).getByText('ENST_SYN45F_1').closest('tr') as HTMLElement;
    expect(within(primaryRow).getByText('Shown on card')).toBeInTheDocument();
    expect(within(primaryRow).queryByText('Filters met')).not.toBeInTheDocument();
  });

  it('says nothing of another transcript when the card’s own met the filters', () => {
    renderOne({ ...keptOnItsSecondTranscript, filter_transcript: null });

    expect(screen.queryByText(/Filters met on/)).not.toBeInTheDocument();
  });

  it('heads the card with the genomic change and its cytoband instead of a locus', async () => {
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
              _id: 'brca2-var',
              chr: '13',
              start: 32316461,
              end: 32316461,
              type: 'SNV',
              gene: 'BRCA2',
              ref: 'G',
              alt: 'A',
              cytoband: '13q13.1',
              rsid: 'rs80359550',
              hgvsc: 'ENST00000380152.8:c.7007G>A',
              transcript_id: 'ENST00000380152.8',
              mane_select: true,
              canonical: true,
              impact: 'MODERATE',
              effect: 'missense_variant',
              genotypes: [],
            }),
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
    expect(screen.getByText('(13q13.1)')).toBeInTheDocument();
    // Labelled as a genomic change, not as HGVS, which it is not normalised to.
    expect(screen.getByText('Genomic change')).toHaveAttribute('title', expect.stringMatching(/Not HGVS-normalised/));
    expect(screen.queryByText('HGVS.g')).not.toBeInTheDocument();
    // The headline does not repeat the position as a locus range.
    expect(screen.queryByText(/32,316,461/)).not.toBeInTheDocument();

    const dbSnp = screen.getByRole('link', { name: 'rs80359550' });
    expect(dbSnp).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/snp/rs80359550');

    // MANE and Canonical sit with the transcript they describe, not with the consequence.
    const transcriptRow = screen
      .getByText('ENST00000380152.8')
      .closest('.variant-card-transcript-row') as HTMLElement;
    expect(within(transcriptRow).getByText('MANE')).toBeInTheDocument();
    expect(within(transcriptRow).getByText('Canonical')).toBeInTheDocument();

    // The consequence names the variant, so it rides with the gene in the headline.
    const headline = screen.getByText('BRCA2').closest('.variant-card-headline') as HTMLElement;
    expect(within(headline).getByText('missense variant')).toBeInTheDocument();
    expect(within(transcriptRow).queryByText('missense variant')).not.toBeInTheDocument();
  });

  it('puts the LoF chip in the headline before the SV second-hit badge', () => {
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
              _id: 'brca2-var',
              chr: '13',
              start: 32316461,
              end: 32316461,
              type: 'SNV',
              gene: 'BRCA2',
              ref: 'G',
              alt: 'A',
              impact: 'HIGH',
              effect: 'stop_gained',
              lof: 'HC',
              transcript_id: 'ENST00000380152.8',
              sv_second_hit: svSecondHit({
                sv_count: 1,
                sv_types: ['DEL'],
                has_deletion: true,
                phase: 'trans',
                phase_evidence: 'read',
                deletion_unmasked: true,
              }),
              genotypes: [],
            }),
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

    // The Frequencies heading is the way to the population data behind its numbers.
    const frequencies = screen.getByRole('link', { name: 'Frequencies' });
    expect(frequencies).toHaveAttribute(
      'href',
      expect.stringContaining('gnomad.broadinstitute.org/variant/'),
    );
    expect(frequencies).toHaveAttribute('target', '_blank');

    // The badge is the way into the SV that caused it, filtered to this gene.
    expect(screen.getByRole('link', { name: /also hit by a deletion/i })).toHaveAttribute(
      'href',
      '/families/F1/structural-variants?gene=BRCA2&project_id=P1',
    );

    const headline = screen.getByText('BRCA2').closest('.variant-card-headline') as HTMLElement;
    const text = [...headline.children].map((node) => node.textContent || '');
    const consequence = text.findIndex((value) => value.includes('stop gained'));
    const lof = text.findIndex((value) => value.includes('LoF HC'));
    const secondHit = text.findIndex((value) => value.includes('SV'));
    expect(consequence).toBeGreaterThan(-1);
    expect(lof).toBeGreaterThan(consequence);
    expect(secondHit).toBeGreaterThan(lof);
  });

  it('splits a multi-accession ID column into separate links', () => {
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
              _id: 'brca2-var',
              chr: '13',
              start: 32316461,
              end: 32316461,
              type: 'SNV',
              gene: 'BRCA2',
              ref: 'G',
              alt: 'A',
              rsid: 'rs201819463,COSV53715949',
              impact: 'MODERATE',
              effect: 'missense_variant',
              genotypes: [],
            }),
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

    expect(screen.getByRole('link', { name: 'rs201819463' })).toHaveAttribute(
      'href',
      'https://www.ncbi.nlm.nih.gov/snp/rs201819463',
    );
    expect(screen.getByRole('link', { name: 'COSV53715949' })).toHaveAttribute(
      'href',
      'https://cancer.sanger.ac.uk/cosmic/search?q=COSV53715949',
    );
    // Not chips — the pill styling belongs to tags, not to identifiers.
    expect(screen.getByRole('link', { name: 'rs201819463' })).toHaveClass('variant-card-id-link');
  });

  it('hides the ID row entirely when the variant has no accession', () => {
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
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
              genotypes: [],
            }),
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

    expect(screen.queryByText('dbSNP')).not.toBeInTheDocument();
    // The rows around it are untouched.
    expect(screen.getByText('Alleles')).toBeInTheDocument();
  });

  it('folds the disclosure into combined context and annotation sections', async () => {
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
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
            }),
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

    // These rows are in two combined sections, not four.
    for (const gone of ['Variant summary', 'Transcript context', 'All scores & constraint', 'Additional annotations']) {
      expect(screen.queryByText(gone)).not.toBeInTheDocument();
    }
    const context = screen
      .getByText('Variant & transcript context')
      .closest('.variant-card-section') as HTMLElement;
    // Gene first, narrowing to the transcript, then the call's own provenance.
    expect(
      [...context.querySelectorAll('dt')].map((node) => node.textContent),
    ).toEqual(['Gene ID', 'Biotype', 'Feature', 'Exon / intron', 'Type / source', 'Phase set']);

    const annotations = screen
      .getByText('Other scores & annotations')
      .closest('.variant-card-section') as HTMLElement;
    expect(within(annotations).getByText('LoF filter')).toBeInTheDocument();
    expect(within(annotations).getByText(/some other field/i)).toBeInTheDocument();
  });

  it("names the VCF's own missense Z apart from the gene profile's", async () => {
    // The import stores a VCF's MISSENSE_Z under the annotation's gene_missense_z, which the
    // disclosure listed as a raw "gene missense z" line, read like the gene profile's value
    // that the In silico column shows as Missense Z.
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'scn1a-var',
            chr: '2',
            start: 166848000,
            end: 166848000,
            type: 'SNV',
            gene: 'SCN1A',
            effect: 'missense_variant',
            gene_missense_z: 5.12,
            annotation_extra: { gene_missense_z: 4.87 },
            genotypes: [],
          }),
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

    const annotations = screen
      .getByText('Other scores & annotations')
      .closest('.variant-card-section') as HTMLElement;
    const row = within(annotations).getByText('Missense Z (gnomAD, from the VCF)').closest('div') as HTMLElement;
    expect(within(row).getByText('4.87')).toBeInTheDocument();
    expect(within(annotations).queryByText(/gene missense z/i)).not.toBeInTheDocument();
    // The gene profile's value stays in the In silico column, under its own name.
    expect(screen.getByText('Missense Z')).toBeInTheDocument();
    expect(screen.getByText('5.12')).toBeInTheDocument();
  });

  it("names the ALT a multi-allelic record's annotation is of, and lists each allele's transcript", async () => {
    // A record with two ALTs holds the CSQ entries of both: the card's annotation is one
    // allele's, and the other allele's entry of the same transcript is listed, not dropped.
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'multi-var',
            chr: '1',
            start: 101,
            end: 101,
            type: 'SNV',
            gene: 'GENE1',
            ref: 'A',
            alt: 'G,T',
            transcript_id: 'ENST00000000001',
            alt_allele: 'T',
            effect: 'stop_gained',
            impact: 'HIGH',
            hgvsc: 'ENST00000000001:c.1A>T',
            transcripts: [
              transcript({
                alt_allele: 'G',
                gene: 'GENE1',
                transcript_id: 'ENST00000000001',
                effect: 'missense_variant',
                impact: 'MODERATE',
                hgvsc: 'ENST00000000001:c.1A>G',
              }),
            ],
            genotypes: [],
          }),
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

    expect(screen.getByText('ALT T')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /2 transcripts/i }));
    const table = within(screen.getByRole('dialog', { name: /GENE1/i })).getByRole('table');
    expect(within(table).getByText('ENST00000000001:c.1A>T')).toBeInTheDocument();
    expect(within(table).getByText('ENST00000000001:c.1A>G')).toBeInTheDocument();
    expect(within(table).getByText('ALT G')).toBeInTheDocument();
    expect(within(table).getByText('ALT T')).toBeInTheDocument();
  });

  it("names VEP's MAX_AF apart from gnomAD's popmax, and a field read as several values", async () => {
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'freq-var',
            chr: '2',
            start: 200,
            end: 200,
            type: 'SNV',
            gene: 'GENE2',
            effect: 'missense_variant',
            gnomad_af: 0.0001,
            population_frequencies: { gnomad_af: 0.0001, gnomad_popmax_af: 0.0004, max_af: 0.012 },
            annotation_extra: { revel_values: '0.12&0.91' },
            genotypes: [],
          }),
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

    expect(screen.getByText('gnomAD popmax')).toBeInTheDocument();
    expect(screen.getByText('MAX_AF (VEP)')).toBeInTheDocument();
    await userEvent.click(screen.getByText('More annotations & detail'));
    const annotations = screen
      .getByText('Other scores & annotations')
      .closest('.variant-card-section') as HTMLElement;
    const row = within(annotations).getByText('revel (several values, none read)').closest('div') as HTMLElement;
    expect(within(row).getByText('0.12&0.91')).toBeInTheDocument();
  });

  it("shows ClinVar's stars and record, SpliceAI's position, gnomAD's FAF95 and AN, and NMD", async () => {
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'fields-var',
            chr: '3',
            start: 300,
            end: 300,
            type: 'SNV',
            ref: 'C',
            alt: 'T',
            gene: 'GENE3',
            effect: 'stop_gained',
            clinvar: 'Pathogenic',
            clinvar_review_status: 'criteria provided, multiple submitters, no conflicts',
            clinvar_stars: 2,
            clinvar_conditions: ['Condition A'],
            clinvar_variation_id: '12345',
            nmd: 'NMD_escaping_variant',
            spliceai_max: 0.82,
            spliceai_ds_dg: 0.82,
            spliceai_dp_dg: 14,
            gnomad_af: 0.0001,
            population_frequencies: { gnomad_af: 0.0001, gnomad_faf95_af: 0.00005 },
            annotation_extra: { gnomad_an: 152000, gnomad_genomes_filter: 'AC0' },
            genotypes: [],
          }),
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

    const clinvar = screen.getByRole('link', { name: /ClinVar\s*Pathogenic ★★☆☆/ });
    expect(clinvar).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/clinvar/variation/12345/');
    expect(clinvar).toHaveAttribute('title', 'criteria provided, multiple submitters, no conflicts — Condition A');
    expect(screen.getByText('0.82 · donor gain +14')).toBeInTheDocument();
    expect(screen.getByText('gnomAD FAF95')).toBeInTheDocument();
    await userEvent.click(screen.getByText('More annotations & detail'));
    expect(screen.getByText('NMD escaping variant')).toBeInTheDocument();
    expect(screen.getByText('152000')).toBeInTheDocument();
    expect(screen.getByText("gnomAD genomes filter (fails gnomAD's own QC)")).toBeInTheDocument();
  });

  it.each([
    ['combined_score', /Priority 0.46/, ['Variant', '0.85']],
    ['variant_score', /Variant score 0.85/, ['Priority', '0.46']],
  ] as const)(
    'shows the priority breakdown on a prioritized variant card ranked by the %s',
    (rankedBy, head, [otherLabel, otherScore]) => {
    // The card leads with the score its rank follows, and lists the other among the metrics.
    renderCards(
        <SmallVariantCards
          variants={[
            smallVariant({
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
            }),
          ]}
          members={[]}
          familyId="F1"
          projectId="P1"
          locationSearch=""
          tags={[]}
          onEditReview={vi.fn()}
          onAcmgClassify={vi.fn()}
          onToggleReviewTag={vi.fn(async () => undefined)}
          rankedBy={rankedBy}
        />,
    );

    expect(screen.getByText(head)).toBeInTheDocument();
    const other = screen.getByText(otherLabel, { selector: '.variant-card-priority-metric-label' });
    expect(other.nextElementSibling).toHaveTextContent(otherScore);
    expect(screen.getByText('#1')).toBeInTheDocument();
    expect(screen.getByText(/Inheritance: homozygous recessive/i)).toBeInTheDocument();
    expect(screen.getByText(/Congenital hypothyroidism/)).toBeInTheDocument();
    },
  );

  // A failed lookup is not a gene without diseases, nor transcripts without CCDS or RefSeq;
  // the failure of the URL given lasts until recover() is called.
  const failLookup = (failingUrl: '/genes/profile' | '/genes/disease-links') => {
    let failing = true;
    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation((url: string, config?: ApiConfig) => {
      if (url === failingUrl && failing) {
        return Promise.reject(Object.assign(new Error('HTTP 503'), { response: { status: 503 } }));
      }
      if (url === '/genes/disease-links') {
        return diseaseLinksAnswer(config, {
          BRCA2: { monarch: [monarchLink('BRCA2-related cancer predisposition', true, ['clingen'], '0700269')] },
        });
      }
      return Promise.resolve({ data: url === '/genes/profile' ? { transcripts: [] } : {} });
    });
    return () => {
      failing = false;
    };
  };

  it('says the gene–disease links could not be loaded, on the card and in its detail', async () => {
    const recover = failLookup('/genes/disease-links');
    const { container } = renderBrca2WithTranscript('ENST00000380152.8');

    expect(
      await within(diseaseLine(container)).findByText(/The gene–disease links could not be loaded\./),
    ).toBeInTheDocument();
    await userEvent.click(screen.getByText('More annotations & detail'));
    expect(
      await screen.findByText(/The gene–disease associations of BRCA2 could not be loaded\./),
    ).toBeInTheDocument();
    expect(screen.queryByText('Loading gene–disease associations…')).not.toBeInTheDocument();

    recover();
    await userEvent.click(within(diseaseLine(container)).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: 'BRCA2-related cancer predisposition' })).toBeInTheDocument();
    expect(diseaseLine(container)).toHaveTextContent('BRCA2-related cancer predisposition');
    expect(screen.queryByText(/could not be loaded/)).not.toBeInTheDocument();
  });

  it('says in the transcript popup that CCDS and RefSeq are unknown when the profile failed', async () => {
    failLookup('/genes/profile');
    renderBrca2WithTranscript('ENST00000380152.8');

    await userEvent.click(screen.getByRole('button', { name: /ENST00000380152\.8/i }));
    const dialog = await screen.findByRole('dialog', { name: /BRCA2/i });

    expect(
      await within(dialog).findByText(
        'The gene’s transcript annotation could not be loaded, so CCDS membership and RefSeq accessions are not shown.',
      ),
    ).toBeInTheDocument();
  });
});

// An excluded variant's card folds to its heading and a bar, unfolds on request, and its
// exclusion can be taken back from the bar; it stays in the list.
describe('SmallVariantCards excluded variant', () => {
  it('folds to its heading, unfolds and undoes the exclusion', async () => {
    const onToggleReviewTag = vi.fn(async () => undefined);
    const excluded = smallVariant({
      _id: 'pkd1-var',
      chr: '16',
      start: 2100000,
      end: 2100000,
      type: 'SNV',
      gene: 'PKD1',
      review: review({ variant_id: 'pkd1-var', tags: ['excluded'] }),
    });
    const { container } = renderCards(
      <SmallVariantCards
        variants={[excluded]}
        members={[{ sample_id: 'P1', role: 'proband', affected: true, sex: 'female' }]}
        familyId="F1"
        locationSearch=""
        tags={[]}
        onEditReview={vi.fn()}
        onAcmgClassify={vi.fn()}
        onToggleReviewTag={onToggleReviewTag}
      />,
    );
    const card = container.querySelector('article') as HTMLElement;
    expect(card).toHaveClass('variant-card--excluded');
    expect(within(card).getByRole('link', { name: 'PKD1' })).toBeInTheDocument();
    expect(card.querySelector('.variant-card-cols')).toBeNull();
    expect(card.querySelector('.variant-card-footer')).toBeNull();

    await userEvent.click(within(card).getByRole('button', { name: /show the excluded card of PKD1/i }));
    expect(card.querySelector('.variant-card-cols')).not.toBeNull();
    expect(within(card).getByRole('button', { name: 'Exclude' })).toHaveAttribute('aria-pressed', 'true');

    await userEvent.click(within(card).getByRole('button', { name: /undo the exclusion of PKD1/i }));
    expect(onToggleReviewTag).toHaveBeenCalledWith(excluded, 'excluded');
  });
});

// The owner's decision of 2026-11-10: a card shows its review's class by the one rule of the
// family page, once: text "VUS" beside the tag of class 4 shows *VUS - class 3* alone, never
// beside *Likely pathogenic - class 4* on one card.
describe('SmallVariantCards review class', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    mockGeneProfile([]);
  });

  const renderReview = (fields: Partial<SmallVariantReview>) =>
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: '1-100-A-G',
            chr: '1',
            start: 100,
            end: 100,
            type: 'SNV',
            gene: 'GENE1',
            review: review({ variant_id: '1-100-A-G', ...fields }),
          }),
        ]}
        members={[]}
        familyId="F1"
        locationSearch=""
        tags={BUILT_IN_REVIEW_TAGS}
        onEditReview={vi.fn()}
        onAcmgClassify={vi.fn()}
        onToggleReviewTag={vi.fn(async () => undefined)}
      />,
    );

  const classLabels = Object.values(ACMG_CLASSIFICATION_LABELS).map((label) => label.toLowerCase());
  // The classes a card shows, each once: its heading's and its tag row's chips that read as a
  // class, or as the text shown.
  const classesOn = (card: HTMLElement, shown: string) => [
    ...new Set(
      Array.from(card.querySelectorAll('.variant-card-headtags .variant-card-chip, .variant-card-tag-row .variant-card-chip'))
        .map((chip) => chip.textContent ?? '')
        .filter((text) => classLabels.includes(text.toLowerCase()) || (shown !== '' && text === shown)),
    ),
  ];

  it.each(reviewClassCases.cases)('shows the class the rule gives, once: $why', ({ classification, tags, shown }) => {
    const { container } = renderReview({ classification, tags });
    const card = container.querySelector('article') as HTMLElement;
    expect(classesOn(card, shown)).toEqual(shown ? [shown] : []);
    if (shown) {
      // In the heading, a class shown from its class tag alone too.
      expect(within(card.querySelector('.variant-card-headtags') as HTMLElement).getByText(shown)).toBeInTheDocument();
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
});
