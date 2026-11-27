import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import SmallVariantCards from '../SmallVariantCards';
import { ACMG_CLASSIFICATION_LABELS, type SmallVariant, type SmallVariantReview } from '../smallVariantSearch';
import api from '../../../lib/api';
import { formatDate } from '../../../lib/format';
import { createTestQueryClient } from '../../../test/createTestQueryClient';
import { BUILT_IN_REVIEW_TAGS } from '../../../test/tagDefinitions';
import { genotype, review, smallVariant, svSecondHit, transcript } from '../../../test/variants';
import reviewClassCases from './fixtures/review-class-cases.json';

vi.mock('../../../lib/api', () => ({ default: { get: vi.fn(), post: vi.fn() } }));

type DiseaseLinks = {
  monarch?: unknown[];
  omim?: unknown[];
  inheritance?: unknown[];
  dosage?: unknown;
  acmg_secondary_finding?: unknown;
};
type ApiConfig = { params?: { symbol?: string[] } };

// GET /genes/disease-links answers one entry per gene asked, with empty lists for a gene
// without a link, as the server does.
const diseaseLinksAnswer = (config: ApiConfig | undefined, links: Record<string, DiseaseLinks> = {}) =>
  Promise.resolve({
    data: (config?.params?.symbol ?? []).map((symbol) => ({
      symbol,
      monarch: links[symbol]?.monarch ?? [],
      omim: links[symbol]?.omim ?? [],
      inheritance: links[symbol]?.inheritance ?? [],
      dosage: links[symbol]?.dosage ?? null,
      acmg_secondary_finding: links[symbol]?.acmg_secondary_finding ?? { status: 'not_listed' },
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

  it('shows one ClinVar chip, CoGA’s release’s value, and never the annotation’s ClinVar', async () => {
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: '1-100-G-A',
            chr: '1',
            start: 100,
            end: 100,
            type: 'SNV',
            ref: 'G',
            alt: 'A',
            gene: 'SYNG1',
            clinvar: 'Likely_pathogenic',
            clinvar_review_status: 'reviewed by expert panel',
            clinvar_stars: 3,
            clinvar_conditions: ['Synthetic disorder 1'],
            clinvar_variation_id: '900001',
            // VEP's cache's classification, kept apart in the annotation: not shown.
            annotation_extra: { clinvar_vep_cache: 'benign' },
            clinvar_coga: [
              {
                alt: 'A',
                status: 'in_release',
                release: '2026-11-22',
                classification: 'Likely_pathogenic',
                review_status: 'reviewed by expert panel',
                stars: 3,
                conditions: ['Synthetic disorder 1'],
                variation_id: 900001,
                included_only: false,
              },
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

    const chip = screen.getByRole('link', { name: /^ClinVar\s*Likely pathogenic ★★★☆$/ });
    expect(chip).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/clinvar/variation/900001/');
    expect(chip).toHaveAttribute('title', 'reviewed by expert panel — Synthetic disorder 1');
    expect(document.querySelectorAll('.variant-card-chip-key')).toHaveLength(1);
    expect(screen.queryByText(/CoGA|annotation,|VEP cache/)).not.toBeInTheDocument();
    await userEvent.click(screen.getByText('More annotations & detail'));
    const status = screen.getByText('ClinVar review status').closest('div') as HTMLElement;
    expect(within(status).getByText('reviewed by expert panel')).toBeInTheDocument();
    expect(screen.queryByText(/clinvar vep cache|ClinVar in the VEP cache/i)).not.toBeInTheDocument();
  });

  it('shows no ClinVar for an allele CoGA’s release does not hold', () => {
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: '1-100-G-A',
            chr: '1',
            start: 100,
            end: 100,
            type: 'SNV',
            ref: 'G',
            alt: 'A',
            gene: 'SYNG1',
            clinvar: null,
            clinvar_coga: [
              {
                alt: 'A',
                status: 'not_in_release',
                release: '2026-11-22',
                classification: null,
                review_status: null,
                stars: null,
                conditions: [],
                variation_id: null,
                included_only: false,
              },
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

    expect(document.querySelector('.variant-card-headtags .variant-card-chip-key')).toBeNull();
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

  const renderBrca2WithTranscript = (transcriptId: string, fields: Partial<SmallVariant> = {}) =>
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
            ...fields,
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
    // Each abbreviated heading shows what it stands for on hover.
    const hints = ['DP', 'AD', 'AF'].map((label) => {
      const heading = within(head).getByText(label);
      fireEvent.mouseEnter(heading);
      const hint = screen.getByRole('tooltip').textContent?.split(':')[0];
      fireEvent.mouseLeave(heading);
      return hint;
    });
    expect(hints).toEqual(['Depth', 'Allelic depth', 'Allele fraction']);
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

  it("leads with the RefSeq transcript MANE pairs with the variant's, then the annotation's matches", async () => {
    mockGeneProfile([
      {
        transcript_id: 'ENST00000380152.8',
        ccds_id: 'CCDS9344.1',
        refseq_accessions: ['NM_000059.4', 'NM_001406720.1'],
      },
    ]);
    renderBrca2WithTranscript('ENST00000380152.8', { mane_select: true, mane_refseq_id: 'NM_000059.4' });
    await userEvent.click(screen.getByRole('button', { name: /ENST00000380152\.8/i }));
    const dialog = await screen.findByRole('dialog', { name: /BRCA2/i });

    const mane = within(dialog).getByTitle('The RefSeq transcript MANE pairs with this one: identical to it');
    expect(mane).toHaveTextContent('NM_000059.4 · MANE');
    expect(
      await within(dialog).findByTitle('RefSeq transcripts the gene annotation matches to this one'),
    ).toHaveTextContent(/^NM_001406720\.1$/);
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

    const first = await within(container).findByRole('link', { name: 'Fanconi anemia complementation group D1' });
    // Causal links first, the better-supported ones first; the line names three and counts
    // the rest of the same kind.
    expect(first.closest('.variant-card-diseases-names')).toHaveTextContent(
      'Fanconi anemia complementation group D1 · breast-ovarian cancer, familial, susceptibility to, 2 · BRCA2-related cancer predisposition',
    );
    // Each name opens the disease in Monarch.
    expect(first).toHaveAttribute('href', 'https://monarchinitiative.org/MONDO:0011366');
    expect(first).toHaveAttribute('target', '_blank');
    expect(
      within(diseaseLine(container)).getByRole('link', { name: 'BRCA2-related cancer predisposition' }),
    ).toHaveAttribute('href', 'https://monarchinitiative.org/MONDO:0700269');
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

    // An OMIM disease, which has no MONDO id, opens in OMIM.
    expect(await within(container).findByRole('link', { name: 'Some syndrome' })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/100100',
    );
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

    const detail = (await screen.findByText(/Gene–disease associations/)).closest(
      '.variant-card-section',
    ) as HTMLElement;
    // Across the card's width: disease names are long.
    expect(detail).toHaveClass('variant-card-section--wide');
    expect(
      await within(detail).findByRole('link', { name: 'BRCA2-related cancer predisposition' }),
    ).toHaveAttribute('href', 'https://monarchinitiative.org/MONDO:0700269');
    // Causal is stated, because it changes how the association reads.
    expect(within(detail).getByText('causal')).toBeInTheDocument();
    expect(within(detail).getByRole('link', { name: 'Breast cancer' })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/114480',
    );
  });

  const curationLine = (container: HTMLElement, index = 0) =>
    container.querySelectorAll<HTMLElement>('.variant-card-gene-curation')[index];

  it('names the gene’s curated modes of inheritance and its ClinGen dosage under its diseases', async () => {
    mockDiseaseLinks({
      GENE1: {
        monarch: [monarchLink('Some disease', true)],
        inheritance: [
          { mode: 'AD', label: 'Autosomal dominant', evidence: ['GenCC Strong'] },
          { mode: 'AR', label: 'Autosomal recessive', evidence: ['ClinGen Definitive', 'GenCC Definitive'] },
        ],
        dosage: {
          haploinsufficiency_score: 3,
          haploinsufficiency: 'Sufficient Evidence for Haploinsufficiency',
          triplosensitivity_score: 0,
          triplosensitivity: 'No Evidence for Triplosensitivity',
          report_url: 'https://search.clinicalgenome.org/kb/gene-dosage/HGNC:1',
        },
      },
      // A disease, but no curated mode: said, as a missing mode is worth knowing.
      GENE2: { monarch: [monarchLink('Other disease', true)] },
      // Neither a disease nor a mode: no line. A dosage curation alone is still shown.
      GENE3: {},
      GENE4: {
        dosage: {
          haploinsufficiency_score: 30,
          haploinsufficiency: 'Gene Associated with Autosomal Recessive Phenotype',
          triplosensitivity_score: null,
          triplosensitivity: null,
          report_url: null,
        },
      },
    });
    const { container } = renderGeneCards(['GENE1', 'GENE2', 'GENE3', 'GENE4']);

    const dominant = await within(container).findByText('Autosomal dominant');
    const line = curationLine(container, 0);
    expect(line).toContainElement(dominant);
    // Dominant and recessive each in a colour of its own, with what states the mode on hover.
    expect(dominant).toHaveClass('variant-card-moi--dominant');
    const recessive = within(line).getByText('Autosomal recessive');
    expect(recessive).toHaveClass('variant-card-moi--recessive');
    expect(recessive).toHaveAttribute('title', 'Autosomal recessive: ClinGen Definitive, GenCC Definitive');
    // ClinGen's dosage scores in a word, the report one click away.
    expect(within(line).getByText('HI 3 · sufficient')).toHaveAttribute(
      'title',
      'Haploinsufficiency: Sufficient Evidence for Haploinsufficiency',
    );
    expect(within(line).getByText('TS 0 · no evidence')).toBeInTheDocument();
    expect(within(line).getByRole('link', { name: 'ClinGen dosage' })).toHaveAttribute(
      'href',
      'https://search.clinicalgenome.org/kb/gene-dosage/HGNC:1',
    );

    expect(curationLine(container, 1)).toHaveTextContent('InheritanceNone curated in GenCC or ClinGen');
    expect(curationLine(container, 1)).not.toHaveTextContent('ClinGen dosage');
    // GENE3 has no line; GENE4's is its dosage alone, without a link or a TS chip.
    expect(container.querySelectorAll('.variant-card-gene-curation')).toHaveLength(3);
    const dosageOnly = curationLine(container, 2);
    expect(dosageOnly).not.toHaveTextContent('Inheritance');
    expect(within(dosageOnly).getByText('HI 30 · AR phenotype')).toBeInTheDocument();
    expect(within(dosageOnly).queryByRole('link')).not.toBeInTheDocument();
    expect(within(dosageOnly).queryByText(/^TS/)).not.toBeInTheDocument();
  });

  it('names the gene’s place on the ACMG SF list, and a list it could not read', async () => {
    mockDiseaseLinks({
      GENE1: {
        acmg_secondary_finding: {
          status: 'listed',
          version: '3.3',
          phenotype: 'Some cardiomyopathy',
          category: 'Cardiovascular',
          inheritance: 'AD',
          variants_to_report: 'All P and LP',
          source_url: 'https://example.test/acmg-sf',
        },
      },
      GENE2: { acmg_secondary_finding: { status: 'unread' } },
      GENE3: { acmg_secondary_finding: { status: 'not_listed' } },
      GENE4: { acmg_secondary_finding: { status: 'not_consulted' } },
    });
    const { container } = renderGeneCards(['GENE1', 'GENE2', 'GENE3', 'GENE4']);

    // On the list: a chip of its own, the list's entry on hover, the list one click away.
    const chip = await within(container).findByRole('link', { name: 'ACMG SF v3.3' });
    expect(chip).toHaveAttribute('href', 'https://example.test/acmg-sf');
    expect(chip).toHaveAttribute(
      'title',
      'On ACMG SF v3.3: Some cardiomyopathy · Cardiovascular · AD · All P and LP',
    );
    expect(curationLine(container, 0)).toContainElement(chip);
    // A list that could not be read says so; a gene off the list, or never checked, shows nothing.
    expect(curationLine(container, 1)).toHaveTextContent('The ACMG SF list could not be read');
    expect(container.querySelectorAll('.variant-card-gene-curation')).toHaveLength(2);
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
    // HGVS on the transcript the card names, without the transcript's or protein's accession.
    expect(screen.getByText('c.7007G>A')).toBeInTheDocument();
    expect(screen.getByText('p.Arg2336His')).toBeInTheDocument();
    expect(screen.queryByText(/ENSP00000369497/)).not.toBeInTheDocument();

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
    // It sits under the card's own transcript, in the variant's column, which has no heading.
    const variantColumn = screen.getByRole('region', { name: 'Variant' });
    expect(within(variantColumn).queryByText('Variant')).not.toBeInTheDocument();
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

  it('shows VEP’s HGVSg as HGVS.g where the annotation has it', () => {
    renderOne(
      smallVariant({
        _id: 'hgvsg',
        chr: '13',
        start: 32316461,
        end: 32316462,
        type: 'INDEL',
        gene: 'BRCA2',
        ref: 'GA',
        alt: 'G',
        hgvsg: 'NC_000013.11:g.32316463del',
        genotypes: [],
      }),
    );
    // VEP's, 3′-shifted on its RefSeq accession, rather than the change derived from the record.
    expect(screen.getByText('NC_000013.11:g.32316463del')).toBeInTheDocument();
    expect(screen.queryByText('chr13:g.32316462del')).not.toBeInTheDocument();
    fireEvent.mouseEnter(screen.getByText('HGVS.g'));
    expect(screen.getByRole('tooltip')).toHaveTextContent(/as VEP wrote it/);
  });

  it('marks each prediction in seqr’s colours, and leaves the gene’s constraint unmarked', () => {
    renderOne(
      smallVariant({
        _id: 'marks',
        chr: '1',
        start: 100,
        end: 100,
        type: 'SNV',
        gene: 'GENE1',
        ref: 'G',
        alt: 'A',
        cadd_phred: 29.4,
        revel: 0.95,
        alpha_missense_pathogenicity: 0.5,
        spliceai_max: 0.05,
        sift: 'deleterious(0)',
        polyphen: 'benign(0.01)',
        gene_pli: 0.99,
        genotypes: [],
      }),
    );
    const silico = screen.getByText('In silico').closest('section') as HTMLElement;
    const markOf = (label: string) =>
      within(silico).getByText(label).nextElementSibling?.querySelector('.variant-card-predictor-mark');

    expect(markOf('CADD')).toHaveClass('variant-card-predictor-mark--pathogenic');
    expect(markOf('REVEL')).toHaveClass('variant-card-predictor-mark--pathogenic-strong');
    expect(markOf('AlphaMissense')).toHaveClass('variant-card-predictor-mark--indeterminate');
    expect(markOf('SpliceAI')).toHaveClass('variant-card-predictor-mark--benign');
    expect(markOf('SIFT')).toHaveClass('variant-card-predictor-mark--pathogenic-supporting');
    expect(markOf('PolyPhen')).toHaveClass('variant-card-predictor-mark--benign');
    expect(markOf('pLI')).toBeNull();
    // The reading is named for a screen reader, and on hover with the thresholds it rests on.
    expect(within(silico).getByRole('img', { name: 'pathogenic, strong' })).toHaveAttribute(
      'title',
      "REVEL 0.95: pathogenic, strong (seqr's thresholds, from ClinGen's PP3/BP4 calibration)",
    );
    expect(markOf('SpliceAI')).toHaveAttribute(
      'title',
      'SpliceAI 0.05: benign (the SpliceAI cut-offs of the ACMG evaluation)',
    );
  });

  it('lists the other annotations across the disclosure’s width, a list of values on one line', async () => {
    const { container } = renderOne(
      smallVariant({
        _id: 'extra',
        chr: '1',
        start: 100,
        end: 100,
        type: 'SNV',
        gene: 'GENE1',
        ref: 'G',
        alt: 'A',
        clinvar_conditions: ['Condition one', 'Condition two'],
        annotation_extra: { clinvar_allele_id: 12345, gnomad_ac: 3, clndn: ['not provided', 'not specified'] },
        genotypes: [],
      }),
    );
    await userEvent.click(screen.getByText('More annotations & detail'));

    const section = screen.getByText('Other scores & annotations').closest('.variant-card-section') as HTMLElement;
    expect(section).toHaveClass('variant-card-section--wide');
    // Under the narrower sections, as the last of the disclosure.
    expect(container.querySelector('.variant-card-more-grid')?.lastElementChild).toBe(section);
    expect(within(section).getByText('Condition one; Condition two')).toBeInTheDocument();
    expect(within(section).getByText('not provided; not specified')).toBeInTheDocument();
    expect(within(section).getByText('12345')).toBeInTheDocument();
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
    // Labelled HGVS.g beside HGVS.c and HGVS.p; its note says it is not HGVS-normalised.
    fireEvent.mouseEnter(screen.getByText('HGVS.g'));
    expect(screen.getByRole('tooltip')).toHaveTextContent(/Not HGVS-normalised/);
    fireEvent.mouseLeave(screen.getByText('HGVS.g'));
    expect(screen.queryByText('Genomic change')).not.toBeInTheDocument();
    // HGVS.c without the transcript accession, which the transcript above names; and only
    // there, not beside the gene in the headline.
    expect(screen.getByText('c.7007G>A')).toBeInTheDocument();
    expect(screen.queryByText(/ENST00000380152\.8:c\./)).not.toBeInTheDocument();
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

  it('puts the gene ID over the transcript, and leaves the disclosure to diseases and other annotations', async () => {
    const { container } = renderCards(
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
              transcript_id: 'ENST00000380152.8',
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

    // The variant column opens with the gene's ID, then the transcript under it.
    const variantColumn = screen.getByRole('region', { name: 'Variant' });
    const geneId = within(variantColumn).getByText('ENSG00000139618');
    expect(variantColumn.firstElementChild).toBe(geneId);
    expect(geneId.nextElementSibling).toHaveClass('variant-card-transcript-row');
    expect(within(geneId.nextElementSibling as HTMLElement).getByText('ENST00000380152.8')).toBeInTheDocument();

    await userEvent.click(screen.getByText('More annotations & detail'));

    // The disclosure holds the gene's diseases and the other annotations, nothing the card
    // already shows.
    expect(
      [...container.querySelectorAll('.variant-card-more-grid .variant-card-section-title')].map(
        (node) => node.textContent,
      ),
    ).toEqual(['Gene–disease associations', 'Other scores & annotations']);

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
            population_frequencies: {
              gnomad_af: 0.0001,
              gnomad_faf95_af: 0.00005,
              gnomad_genomes_faf95_af: 0.00004,
            },
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
    // A site gnomAD's own QC fails is named under the frequencies, collapsed or not.
    const frequencies = screen.getByText('Frequencies').closest('.variant-card-col') as HTMLElement;
    const filterRow = within(frequencies).getByText('genomes filter').closest('div') as HTMLElement;
    expect(within(filterRow).getByText("AC0 (fails gnomAD's own QC)")).toBeInTheDocument();
    await userEvent.click(screen.getByText('More annotations & detail'));
    const annotations = screen
      .getByText('Other scores & annotations')
      .closest('.variant-card-section') as HTMLElement;
    expect(within(annotations).getByText('NMD escaping variant')).toBeInTheDocument();
    const anRow = within(annotations).getByText('gnomAD allele number').closest('div') as HTMLElement;
    expect(within(anRow).getByText('152000')).toBeInTheDocument();
    // A frequency the column leaves out is listed here, the column's own ones are not.
    const fafRow = within(annotations).getByText('gnomAD genomes FAF95').closest('div') as HTMLElement;
    expect(within(fafRow).getByText('4.0e-5')).toBeInTheDocument();
    expect(within(annotations).queryByText(/gnomad faf95 af/i)).not.toBeInTheDocument();
    expect(within(annotations).queryByText(/genomes filter/i)).not.toBeInTheDocument();
  });

  it('links a ClinVar classification without a VariationID by the rsID', () => {
    renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'cache-var',
            chr: '1',
            start: 100,
            end: 145,
            type: 'INDEL',
            gene: 'GENE1',
            effect: 'intron_variant',
            clinvar: 'Pathogenic',
            rsid: 'rs123',
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

    const clinvar = screen.getByRole('link', { name: /^ClinVar\s*Pathogenic$/ });
    expect(clinvar).toHaveAttribute('href', 'https://www.ncbi.nlm.nih.gov/clinvar/?term=rs123');
  });

  // The card's class sets its rail and, on a small variant's card alone, its background
  // (clinvarCardTintRules.test.ts): red for (likely) pathogenic, orange (warning) for a conflict
  // that lists a (likely) pathogenic classification, green for (likely) benign.
  it.each([
    ['Pathogenic', 'pathogenic'],
    ['Likely_pathogenic', 'pathogenic'],
    ['Pathogenic,_low_penetrance', 'pathogenic'],
    ['uncertain_significance&likely_pathogenic', 'warning'],
    ['Benign', 'benign'],
    ['Likely_benign', 'benign'],
    ['Uncertain_significance', 'neutral'],
    ['Conflicting_classifications_of_pathogenicity', 'neutral'],
    ['risk_factor', 'neutral'],
    [undefined, 'neutral'],
  ])("marks the card of a variant ClinVar reads as %s as %s", (clinvar, tone) => {
    const { container } = renderCards(
      <SmallVariantCards
        variants={[
          smallVariant({
            _id: 'tint-var',
            chr: '1',
            start: 100,
            end: 100,
            type: 'SNV',
            ref: 'A',
            alt: 'G',
            gene: 'GENE1',
            clinvar,
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

    const card = container.querySelector('article.variant-card') as HTMLElement;
    expect(card).toHaveClass('small-variant-card', `variant-card--clinvar-${tone}`);
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
    expect(
      await within(diseaseLine(container)).findByRole('link', { name: 'BRCA2-related cancer predisposition' }),
    ).toBeInTheDocument();
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

// An analyst going through the list folds each card when done with it, from its foot or from the
// caret before its heading, and opens it again from that caret. The fold is the analyst's view
// only: it touches no review.
describe('SmallVariantCards collapse', () => {
  const brca2 = (id: string, hgvsc: string, hgvsp: string) =>
    smallVariant({
      _id: id,
      chr: '13',
      start: 32340000,
      end: 32340000,
      type: 'SNV',
      gene: 'BRCA2',
      ref: 'A',
      alt: 'G',
      hgvsc: `ENST00000380152.8:${hgvsc}`,
      hgvsp: `ENSP00000369497.3:${hgvsp}`,
    });
  const renderTwo = (onToggleReviewTag = vi.fn(async () => undefined), onEditReview = vi.fn()) =>
    renderCards(
      <SmallVariantCards
        variants={[brca2('first', 'c.100A>G', 'p.Lys34Glu'), brca2('second', 'c.200A>G', 'p.Asn67Asp')]}
        members={[{ sample_id: 'P1', role: 'proband', affected: true, sex: 'female' }]}
        familyId="F1"
        locationSearch=""
        tags={[]}
        onEditReview={onEditReview}
        onAcmgClassify={vi.fn()}
        onToggleReviewTag={onToggleReviewTag}
      />,
    );

  beforeEach(() => {
    vi.resetAllMocks();
    mockGeneProfile([]);
  });

  it('folds a card from its foot to its heading and change, and opens it from the caret', async () => {
    const onToggleReviewTag = vi.fn(async () => undefined);
    const onEditReview = vi.fn();
    const { container } = renderTwo(onToggleReviewTag, onEditReview);
    const [first, second] = Array.from(container.querySelectorAll('article')) as HTMLElement[];

    await userEvent.click(within(first).getByRole('button', { name: 'Collapse' }));
    expect(first).toHaveClass('variant-card--collapsed');
    expect(first.querySelector('.variant-card-cols')).toBeNull();
    expect(first.querySelector('.variant-card-footer')).toBeNull();
    expect(within(first).getByRole('link', { name: 'BRCA2' })).toBeInTheDocument();
    // The change, without its accession, tells it from the other BRCA2 card.
    expect(within(first).getByText('c.100A>G · p.Lys34Glu')).toBeInTheDocument();
    // Not an excluded card, and the next card stays open.
    expect(first).not.toHaveClass('variant-card--excluded');
    expect(within(first).queryByText('Excluded')).not.toBeInTheDocument();
    expect(second.querySelector('.variant-card-cols')).not.toBeNull();

    const caret = within(first).getByRole('button', { name: 'Expand the card of BRCA2' });
    expect(caret).toHaveAttribute('aria-expanded', 'false');
    await userEvent.click(caret);
    expect(first).not.toHaveClass('variant-card--collapsed');
    expect(first.querySelector('.variant-card-cols')).not.toBeNull();
    expect(within(first).queryByText('c.100A>G · p.Lys34Glu')).not.toBeInTheDocument();

    await userEvent.click(within(first).getByRole('button', { name: 'Collapse the card of BRCA2' }));
    expect(first.querySelector('.variant-card-cols')).toBeNull();

    expect(onToggleReviewTag).not.toHaveBeenCalled();
    expect(onEditReview).not.toHaveBeenCalled();
  });

  it('opens and folds an excluded card from the same caret as its bar', async () => {
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
        members={[]}
        familyId="F1"
        locationSearch=""
        tags={[]}
        onEditReview={vi.fn()}
        onAcmgClassify={vi.fn()}
        onToggleReviewTag={vi.fn(async () => undefined)}
      />,
    );
    const card = container.querySelector('article') as HTMLElement;
    expect(card).toHaveClass('variant-card--collapsed');

    await userEvent.click(within(card).getByRole('button', { name: 'Expand the card of PKD1' }));
    expect(card.querySelector('.variant-card-cols')).not.toBeNull();
    expect(within(card).getByRole('button', { name: /hide the excluded card of PKD1/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    );

    // Folded from its foot, it is the excluded heading and bar again.
    await userEvent.click(within(card).getByRole('button', { name: 'Collapse' }));
    expect(card.querySelector('.variant-card-cols')).toBeNull();
    expect(within(card).getByText('Excluded')).toBeInTheDocument();
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

  it('puts the tags and the note first under the heading, each with who wrote it and when', () => {
    const { container } = renderReview({
      tags: ['acmg_vus_warm', 'secondary_finding', 'segregates'],
      tag_metadata: {
        acmg_vus_warm: { updated_by: 'supervisor', updated_at: '2026-11-21T10:00:00Z' },
        secondary_finding: { updated_by: 'supervisor', updated_at: '2026-11-21T10:00:00Z' },
        segregates: { updated_by: 'analyst', updated_at: '2026-11-20T09:00:00Z' },
      },
      note: 'Confirmed by Sanger.',
      note_updated_by: 'analyst',
      note_updated_at: '2026-11-22T11:00:00Z',
    });
    const card = container.querySelector('article') as HTMLElement;
    const block = card.querySelector('.variant-card-review') as HTMLElement;
    // Straight under the heading, before what the gene and the variant are.
    expect(card.querySelector('.variant-card-head')?.nextElementSibling).toBe(block);
    // The tags one save added together, with who added them and when; then the other save's
    // (a tag the family's list does not define, by its key).
    const saves = Array.from(block.querySelectorAll('.variant-card-review-tags')).map((save) => save.textContent);
    expect(saves).toEqual([
      `VUS — warmSecondary findingsupervisor · ${formatDate('2026-11-21T10:00:00Z')}`,
      `segregatesanalyst · ${formatDate('2026-11-20T09:00:00Z')}`,
    ]);
    // The note, with who wrote it and when.
    expect(within(block).getByText('Confirmed by Sanger.')).toHaveTextContent(
      `Confirmed by Sanger. analyst · ${formatDate('2026-11-22T11:00:00Z')}`,
    );
  });

  it('shows no review block on a card whose review holds no tag or note', () => {
    // The class is the heading's.
    const { container } = renderReview({ classification: 'VUS' });
    expect(container.querySelector('.variant-card-review')).toBeNull();
  });

  it('names who pressed Review and Report, and when, beside their pressed buttons', () => {
    const { container } = renderReview({
      tags: ['review', 'report'],
      tag_metadata: {
        review: { updated_by: 'analyst', updated_at: '2026-11-20T09:00:00Z' },
        report: { updated_by: 'supervisor', updated_at: '2026-11-21T10:00:00Z' },
      },
    });
    const card = container.querySelector('article') as HTMLElement;
    const saves = Array.from(card.querySelectorAll('.variant-card-review-tags')).map((save) => save.textContent);
    // In the catalogue's order: the test's lists Report and not Review, shown by its key after it.
    expect(saves).toEqual([
      `Reportsupervisor · ${formatDate('2026-11-21T10:00:00Z')}`,
      `reviewanalyst · ${formatDate('2026-11-20T09:00:00Z')}`,
    ]);
    expect(within(card).getByRole('button', { name: 'Report' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('names who excluded the variant, and when, on the bar of its folded card', () => {
    const { container } = renderReview({
      tags: ['excluded'],
      tag_metadata: { excluded: { updated_by: 'analyst', updated_at: '2026-11-20T09:00:00Z' } },
    });
    const card = container.querySelector('article') as HTMLElement;
    expect(card.querySelector('.variant-card-excluded-bar')).toHaveTextContent(
      `Excludedanalyst · ${formatDate('2026-11-20T09:00:00Z')}`,
    );
    // Not again among the tags under the heading.
    expect(card.querySelector('.variant-card-review')).toBeNull();
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
