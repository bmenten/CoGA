import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi, type Mock } from 'vitest';

import GeneInfoPage from '../GeneInfoPage';
import api from '../../../lib/api';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

vi.mock('../../../lib/api', () => ({
  default: {
    get: vi.fn(),
    post: vi.fn(),
  },
}));

const renderPage = (initialEntry = '/genes?gene=BRCA1') => {
  const queryClient = createTestQueryClient();

  return render(
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[initialEntry]}>
        <GeneInfoPage />
      </MemoryRouter>
    </QueryClientProvider>
  );
};

type ProfileFixture = {
  symbol: string;
  source_status: Record<string, Record<string, unknown>>;
  extra: Record<string, unknown>;
};

// Opens another gene's page, its profile the BRCA1 fixture below as changed by `change`.
const renderGeneFrom = (symbol: string, change: (profile: ProfileFixture) => void) => {
  const get = api.get as unknown as Mock;
  const brca1 = get.getMockImplementation()!;
  get.mockImplementation(async (url: string, config?: { params?: Record<string, unknown> }) => {
    if (url !== '/genes/profile') return brca1(url, config);
    expect(config?.params?.symbol).toBe(symbol);
    const { data } = (await brca1(url, { params: { symbol: 'BRCA1' } })) as { data: ProfileFixture };
    const profile = JSON.parse(JSON.stringify(data)) as ProfileFixture;
    profile.symbol = symbol;
    change(profile);
    return { data: profile };
  });
  return renderPage(`/genes?gene=${symbol}`);
};

const acmgSecondaryFindingRow = () =>
  screen.getByText('ACMG secondary finding').closest('.gene-compact-detail-row') as HTMLElement;

describe('GeneInfoPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    localStorage.setItem('role', 'admin');

    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (url: string, config?: { params?: Record<string, unknown> }) => {
        if (url === '/genes/search') {
          return Promise.resolve({
            data: [
              {
                symbol: 'BRCA1',
                gene_id: 'TX1',
                chr: '17',
                start: 43044295,
                end: 43125482,
                transcript_count: 2,
                assembly_count: 2,
              },
            ],
          });
        }

        if (url === '/genes/profile') {
          expect(config?.params?.symbol).toBe('BRCA1');
          return Promise.resolve({
            data: {
              assembly_id: 'assembly-1',
              assembly_name: 'GRCh38',
              assembly_version: 'p14',
              species_name: 'Homo sapiens',
              symbol: 'BRCA1',
              gene_id: 'TX1',
              display_name: 'BRCA1 DNA repair associated',
              summary: 'Tumor suppressor involved in DNA repair.',
              chr: '17',
              start: 43044295,
              end: 43125482,
              strand: -1,
              biotype: 'protein_coding',
              transcript_count: 2,
              transcripts: [
                {
                  transcript_id: 'ENST00000357654.9',
                  start: 43044295,
                  end: 43125482,
                  exon_count: 24,
                  strand: -1,
                  biotype: 'protein_coding',
                  source: 'gencode',
                  // GENCODE states these per transcript; the overview rows and the
                  // badges both read them rather than a gene-level scraped value.
                  mane_select: true,
                  ensembl_canonical: true,
                  refseq_accessions: ['NM_007294.4'],
                  ccds_id: 'CCDS11453.1',
                },
                {
                  transcript_id: 'NM_007294.4',
                  start: 43044295,
                  end: 43125482,
                  exon_count: 24,
                  strand: -1,
                  biotype: 'protein_coding',
                  source: 'refgene',
                  mane_plus_clinical: true,
                },
              ],
              aliases: ['BRCC1'],
              previous_symbols: ['RNF53'],
              ensembl_gene_id: 'ENSG00000012048',
              ncbi_gene_id: '672',
              hgnc_id: 'HGNC:1100',
              omim_gene_id: '113705',
              gene_type: 'protein-coding gene',
              location: '17q21.31',
              assembly_locations: [
                {
                  assembly_id: 'assembly-1',
                  assembly_name: 'GRCh38',
                  assembly_version: 'p14',
                  chr: '17',
                  start: 43044295,
                  end: 43125482,
                  transcript_count: 2,
                  is_primary: true,
                  is_family_context: false,
                },
                {
                  assembly_id: 'assembly-2',
                  assembly_name: 'T2T-CHM13',
                  assembly_version: 'v2.0',
                  chr: '17',
                  start: 43110000,
                  end: 43190000,
                  transcript_count: 1,
                  is_primary: false,
                  is_family_context: false,
                },
              ],
              homologs: [
                {
                  species_name: 'Mus Musculus',
                  common_name: 'mouse',
                  symbol: 'Brca1',
                  ensembl_gene_id: 'ENSMUSG00000017146',
                  homology_type: 'ortholog_one2one',
                  percent_id: 81.2,
                  percent_coverage: 96.4,
                  in_platform: true,
                },
              ],
              panels: [{ panel_id: 'panel-1', name: 'Breast cancer', gene_count: 12 }],
              family_counts: {
                small_variants: 4,
                structural_variants: 1,
              },
              gene_annotation_source: 'gencode v50 (Ensembl 116)',
              gene_annotation_imported_at: '2026-08-10T20:30:00Z',
              source_status: {
                hgnc: {
                  status: 'success',
                  fetched_at: '2026-03-27T10:00:00Z',
                  source_url: 'https://rest.genenames.org/fetch/symbol/BRCA1',
                  message: null,
                  payload: { name: 'BRCA1 DNA repair associated' },
                },
                clingen: {
                  status: 'success',
                  fetched_at: '2026-03-27T11:00:00Z',
                  source_url: 'https://search.clinicalgenome.org/kb/genes/HGNC%3A1100',
                  message: null,
                  payload: {
                    curation_counts: {
                      gene_disease_validity: 12,
                      dosage_sensitivity: 3,
                      clinical_actionability: 2,
                    },
                  },
                },
                dbnsfp_gene: {
                  status: 'success',
                  fetched_at: '2026-03-27T12:00:00Z',
                  source_url: '/data/ref-data/dbNSFP5.4_gene.gz',
                  message: null,
                  release: '5.4',
                  release_detail: { label: '5.4', checksum: 'abc123def456789' },
                  payload: { record_count: 1 },
                },
                hgnc_complete_set: {
                  status: 'success',
                  fetched_at: '2026-08-10T20:00:00Z',
                  source_url: 'https://example.test/hgnc',
                  message: null,
                  release: '2026-08-07',
                  payload: {},
                },
                // The ACMG SF list CoGA ships, as the gene reference sync records it.
                acmg_sf: {
                  status: 'success',
                  fetched_at: '2026-10-14T09:00:00Z',
                  source_url: '/data/ref-data/acmg-sf/acmg-sf-genes.json',
                  message: null,
                  release: 'v3.3',
                  release_detail: { label: 'v3.3', checksum: 'ee61f8b98357a728', size_bytes: 24007 },
                  payload: { symbols_with_records: 84, record_count: 1 },
                },
              },
              external_links: [
                { label: 'Ensembl', href: 'https://www.ensembl.org/id/ENSG00000012048' },
                { label: 'ClinGen', href: 'https://search.clinicalgenome.org/kb/genes/HGNC%3A1100' },
                { label: 'gnomAD', href: 'https://gnomad.broadinstitute.org/gene/ENSG00000012048?dataset=gnomad_r4' },
                { label: 'DECIPHER', href: 'https://www.deciphergenomics.org/gene/BRCA1' },
                { label: 'OMIM', href: 'https://www.omim.org/entry/113705' },
                { label: 'PubMed', href: 'https://pubmed.ncbi.nlm.nih.gov/?term=BRCA1' },
                { label: 'ClinVar', href: 'https://www.ncbi.nlm.nih.gov/clinvar/?term=BRCA1%5Bgene%5D' },
              ],
              monarch_associations: [
                {
                  mondo_id: 'MONDO:0011623',
                  disease_label: 'Fanconi anemia complementation group S',
                  predicate: 'causes',
                  predicates: ['causes', 'gene_associated_with_condition'],
                  sources: ['omim', 'orphanet'],
                  causal: true,
                  monarch_url: 'https://monarchinitiative.org/MONDO:0011623',
                  phenotype_count: 42,
                  matched_phenotypes: [{ hpo_id: 'HP:0002894', label: 'Pancreatic carcinoma' }],
                },
              ],
              extra: {
                hgnc_gene_group: ['DNA repair'],
                // No ensembl_canonical_transcript / clingen mane_select_transcript here:
                // those came from a per-gene Ensembl call and a ClinGen page scrape that
                // no longer run, and were absent for all but a handful of genes even
                // when they did. The transcript flags are the only source now.
                ensembl_description: 'BRCA1 DNA repair associated',
                ncbi_other_designations: ['breast cancer type 1 susceptibility protein'],
                // As the API actually returns it for a dbNSFP-covered gene: HGNC's
                // identifiers arrive in their own block, which the Vega row reads.
                hgnc_identifiers: { vega_id: 'OTTHUMG00000157426', mgd_id: 'MGI:104537' },
                refseq_accessions: ['NM_007294.4'],
                omim_diseases: [
                  {
                    label: 'Breast-ovarian cancer, familial, susceptibility to, 1',
                    omim_id: '604370',
                  },
                ],
                dbnsfp_disease_associations: [
                  {
                    label: 'Hereditary breast and ovarian cancer syndrome',
                    source: 'ClinVar',
                    significance: 'Pathogenic',
                  },
                ],
                hpo_terms: [
                  { hpo_id: 'HP:0003002', label: 'Breast carcinoma' },
                  { hpo_id: 'HP:0002896', label: 'Neoplasm of the liver' },
                ],
                dbnsfp_orphanet_assertions: [
                  {
                    orphanet_id: '145',
                    disease_title: 'Hereditary breast and/or ovarian cancer syndrome',
                    association_type: 'Disease-causing germline mutation(s) in',
                  },
                ],
                dbnsfp_trait_associations: ['Breast neoplasm', 'Ovarian neoplasm'],
                gencc_assertions: [
                  {
                    gencc_id: 'GENCC:0000001',
                    disease_title: 'breast-ovarian cancer, familial, susceptibility to, 1',
                    classification_title: 'Definitive',
                    moi_title: 'Autosomal dominant',
                    pmids: ['12345678'],
                  },
                ],
                clingen_dosage_assertions: [
                  // dbNSFP's copy of ClinGen's haploinsufficiency curation.
                  {
                    haploinsufficiency: '3',
                    description: 'Sufficient evidence for dosage pathogenicity',
                    disease: 'BRCA1-related cancer predisposition',
                    pmids: ['32375709'],
                  },
                  // ClinGen's own dosage file, as parse_clingen_dosage_rows writes it.
                  {
                    hgnc_id: 'HGNC:1100',
                    haploinsufficiency: 'Sufficient Evidence for Haploinsufficiency',
                    triplosensitivity: 'No Evidence for Triplosensitivity',
                    online_report: 'https://search.clinicalgenome.org/kb/gene-dosage/HGNC:1100',
                    date: '2021-09-23T08:27:44-04:00',
                  },
                ],
                constraint_metrics: {
                  missense_z: 3.21,
                  shet: 0.094,
                  phaplo: 0.88,
                  ptriplo: 0.06,
                  p_hi: 0.134,
                  p_rec: 0.021,
                  gnomad_pli: 1.0,
                  gnomad_prec: 0.002,
                  gnomad_pnull: 0.0,
                  gnomad_lof_oe: 0.061,
                  gnomad_mis_oe: 0.27,
                  gnomad_loeuf: 0.16,
                  gnomad_moeuf: 0.54,
                  gdi: 72.4,
                  gdi_phred: 48.2,
                  loftool_score: 0.002,
                },
                gene_ontology: {
                  biological_process: [
                    'double-strand break repair via homologous recombination',
                    'DNA repair',
                    'DNA recombination',
                    'response to ionizing radiation',
                    'DNA damage response',
                  ],
                  cellular_component: ['nucleus'],
                  molecular_function: ['ubiquitin-protein transferase activity'],
                },
                dbnsfp_pathways: {
                  uniprot: ['Protein modification'],
                  consensus_path_db: ['DNA repair pathway'],
                  kegg: ['Ubiquitin mediated proteolysis'],
                  kegg_ids: ['hsa04120'],
                },
                dbnsfp_tissue_expression: {
                  tissue_specificity: 'Expressed in several tissues with enriched immune expression.',
                  hpa_highly_expressed: ['thymus', 'testis', 'bone marrow'],
                  hpa_consensus_tpm: {
                    thymus: 13.5,
                    testis: 11.6,
                    bone_marrow: 10.9,
                    breast: 3.3,
                    ovary: 2.7,
                  },
                },
                dbnsfp_model_organisms: {
                  mouse: {
                    symbol: 'Brca1',
                    phenotypes: ['neoplasm', 'embryo phenotype'],
                  },
                  zebrafish: {
                    symbol: 'brca1',
                    structures: ['nervous system'],
                    phenotype_tags: ['abnormal'],
                  },
                },
                clingen_curation_counts: {
                  gene_disease_validity: 12,
                  dosage_sensitivity: 3,
                },
                clingen_gene_facts: {
                  hgnc_name: 'BRCA1 DNA repair associated',
                  previous_symbols: ['RNF53'],
                  alias_symbols: ['BRCC1'],
                  gencc_classifications: {
                    Definitive: 5,
                    Strong: 1,
                  },
                  haploinsufficiency_index: 13.4,
                  pli: 1.0,
                  loeuf: 0.16,
                  function: 'DNA repair and double-strand break signaling.',
                },
                // BRCA1's entry in data/ref-data/acmg-sf/acmg-sf-genes.json, as the sync
                // stores it.
                acmg_secondary_finding: {
                  list: 'ACMG SF',
                  version: '3.3',
                  gene: 'BRCA1',
                  hgnc_id: 'HGNC:1100',
                  phenotype: 'Hereditary breast and/or ovarian cancer',
                  omim_disorders: ['604370'],
                  category: 'Cancer Predisposition',
                  inheritance: 'AD',
                  variants_to_report: 'All P and LP',
                  added_in: '1.0',
                  notes: [],
                  source_url: 'https://doi.org/10.1016/j.gim.2025.101454',
                },
              },
              updated_at: '2026-03-27T10:00:00Z',
            },
          });
        }

        throw new Error(`Unexpected GET ${url}`);
      },
    );

    (api.post as unknown as ReturnType<typeof vi.fn>).mockResolvedValue({ data: {} });
  });

  it('renders a condensed gene explorer overview with evidence, transcripts, and requested links', async () => {
    renderPage();

    expect(await screen.findByRole('heading', { name: 'BRCA1' })).toBeInTheDocument();
    expect(
      screen.getAllByText(/tumor suppressor involved in dna repair/i).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText(/hg38 chr17:43,044,295-43,125,482/i)).toBeInTheDocument();
    expect(screen.getByText(/t2t chr17:43,110,000-43,190,000/i)).toBeInTheDocument();
    expect(screen.getAllByText(/17q21.31/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/aliases/i)).toBeInTheDocument();
    expect(screen.getByText(/BRCC1/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '113705' })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/113705',
    );
    expect(
      screen.getByRole('link', {
        name: /Breast-ovarian cancer, familial, susceptibility to, 1/i,
      }),
    ).toHaveAttribute('href', 'https://www.omim.org/entry/604370');
    expect(
      screen.getByText(/Hereditary breast and ovarian cancer syndrome/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/ClinVar · Pathogenic/i)).toBeInTheDocument();
    expect(screen.getByText(/Monarch gene.disease associations/i)).toBeInTheDocument();
    const monarchLink = screen.getByRole('link', {
      name: /Fanconi anemia complementation group S/i,
    });
    expect(monarchLink).toHaveAttribute(
      'href',
      'https://monarchinitiative.org/MONDO:0011623',
    );
    expect(
      screen.getByText(/Causes · omim, orphanet · 42 phenotypes · 1 observed in family/i),
    ).toBeInTheDocument();
    expect(screen.getByText('Pancreatic carcinoma')).toBeInTheDocument();
    expect(screen.getByText('Breast carcinoma')).toBeInTheDocument();
    expect(screen.getByText('HP:0003002')).toBeInTheDocument();
    expect(
      screen.getByText(/Hereditary breast and\/or ovarian cancer syndrome/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Disease-causing germline mutation/i)).toBeInTheDocument();
    expect(screen.getAllByText(/gene-disease validity/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/DECIPHER %HI/i)).toBeInTheDocument();
    expect(screen.getByText(/Sufficient evidence for dosage pathogenicity/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'PMID 32375709' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/32375709/',
    );
    expect(screen.getByText(/HI score 3 · Sufficient evidence .* · via dbNSFP/)).toBeInTheDocument();
    // ClinGen's own dosage file: both curations in words, the curation's day and its report,
    // not a second "ClinGen haploinsufficiency" item that calls the words an HI score (CLIN-19).
    expect(screen.getByText('ClinGen dosage curation')).toBeInTheDocument();
    expect(
      screen.getByText(
        'Haploinsufficiency: Sufficient Evidence for Haploinsufficiency · ' +
          'Triplosensitivity: No Evidence for Triplosensitivity · curated 2021-09-23',
      ),
    ).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'ClinGen report' })).toHaveAttribute(
      'href',
      'https://search.clinicalgenome.org/kb/gene-dosage/HGNC:1100',
    );
    expect(screen.queryByText(/HI score Sufficient/)).not.toBeInTheDocument();
    // BRCA1 is on the ACMG SF list: the row names the list's version, linked to the policy
    // statement, and the gene's phenotype and category as the list states them. The two
    // ClinGen count rows that no source fills are gone (CLIN-19).
    const acmgRow = acmgSecondaryFindingRow();
    expect(acmgRow).toHaveTextContent('On ACMG SF v3.3');
    expect(within(acmgRow).getByRole('link', { name: 'ACMG SF v3.3' })).toHaveAttribute(
      'href',
      'https://doi.org/10.1016/j.gim.2025.101454',
    );
    expect(
      within(acmgRow).getByText(
        'Hereditary breast and/or ovarian cancer · Cancer Predisposition · AD · All P and LP',
      ),
    ).toBeInTheDocument();
    expect(screen.queryByText('Clinical actionability')).not.toBeInTheDocument();
    expect(screen.queryByText('Variant pathogenicity')).not.toBeInTheDocument();
    const vegaRow = screen.getByText('Vega').closest('.gene-compact-detail-row');
    expect(vegaRow).not.toBeNull();
    expect(within(vegaRow as HTMLElement).getByText('OTTHUMG00000157426')).toBeInTheDocument();
    // Gene-level RefSeq accessions in the overview. Scoped, because the accession now
    // also appears in the transcript table twice — as a transcript and as a mapping.
    const refseqRow = screen.getByText('RefSeq accessions').closest('.gene-compact-detail-row');
    expect(within(refseqRow as HTMLElement).getByText('NM_007294.4')).toBeInTheDocument();
    expect(screen.getByText('3.21')).toBeInTheDocument();
    expect(screen.getByText('0.094')).toBeInTheDocument();
    expect(screen.getByText('0.880')).toBeInTheDocument();
    expect(screen.getByText('0.060')).toBeInTheDocument();
    expect(
      screen.getByText(/double-strand break repair via homologous recombination/i),
    ).toBeInTheDocument();
    expect(screen.getByText(/Ubiquitin mediated proteolysis/i)).toBeInTheDocument();
    expect(screen.getByText(/Expressed in several tissues/i)).toBeInTheDocument();
    expect(screen.getAllByText(/Thymus/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/13.5 TPM/i)).toBeInTheDocument();
    expect(screen.getByText(/Mouse: Brca1/i)).toBeInTheDocument();
    expect(screen.queryByText(/DNA damage response/i)).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /show more function context/i }));
    expect(screen.getByText(/DNA damage response/i)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /show more disease evidence/i }));
    expect(screen.getByText(/Definitive · Autosomal dominant/i)).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'PMID 12345678' })).toHaveAttribute(
      'href',
      'https://pubmed.ncbi.nlm.nih.gov/12345678/',
    );
    expect(screen.getByText('Breast neoplasm')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /show advanced constraint metrics/i }));
    expect(screen.getByText(/gnomAD missense OE/i)).toBeInTheDocument();
    expect(screen.getByText('0.270')).toBeInTheDocument();
    expect(screen.getByText(/GDI Phred/i)).toBeInTheDocument();
    expect(screen.getByText('48.20')).toBeInTheDocument();
    expect(screen.getByText(/LoFtool score/i)).toBeInTheDocument();
    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: /show 2 transcripts/i }));
    const transcriptTable = screen.getByRole('table');
    expect(transcriptTable).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /hide 2 transcripts/i })).toHaveAttribute(
      'aria-expanded',
      'true',
    );
    expect(within(transcriptTable).getByText('ENST00000357654.9')).toBeInTheDocument();
    // The Ensembl transcript's row also names its RefSeq equivalent, and links to it.
    // Scoped to that row: NM_007294.4 is separately a transcript of its own here.
    const ensemblRow = within(transcriptTable)
      .getByText('ENST00000357654.9')
      .closest('tr') as HTMLElement;
    expect(within(ensemblRow).getByRole('link', { name: 'NM_007294.4' })).toHaveAttribute(
      'href',
      'https://www.ncbi.nlm.nih.gov/nuccore/NM_007294.4',
    );
    expect(within(transcriptTable).getByText('MANE Select')).toBeInTheDocument();
    // CCDS membership is a per-transcript signal, so it is badged on the transcript
    // rather than shown once for the gene.
    expect(within(transcriptTable).getByText('CCDS')).toBeInTheDocument();
    expect(within(transcriptTable).getByText('Ensembl Canonical')).toBeInTheDocument();
    // The overview names the transcripts too, not just the badges in the table. Both
    // read the per-transcript GENCODE flags — the gene-level values these rows used to
    // take came from a ClinGen scrape and an Ensembl call that no longer run, and were
    // absent for all but a handful of genes even when they did.
    // MANE Plus Clinical is rare but clinically important, so the overview names it
    // beside the other two rather than leaving it to a badge further down.
    // The label also exists as a badge in the transcript table, so match the term.
    const plusClinicalRow = screen
      .getByText('MANE Plus Clinical', { selector: 'dt' })
      .closest('.gene-compact-detail-row');
    expect(
      within(plusClinicalRow as HTMLElement).getByText('NM_007294.4'),
    ).toBeInTheDocument();
    for (const label of ['MANE transcript', 'Canonical transcript']) {
      const row = screen.getByText(label).closest('.gene-compact-detail-row');
      expect(row).not.toBeNull();
      expect(within(row as HTMLElement).getByText('ENST00000357654.9')).toBeInTheDocument();
    }
    // RefSeq Select is derived from the HGNC refseq accessions. The RefSeq transcript
    // has a row of its own, distinct from the mapping shown on the Ensembl row above.
    const refseqTranscriptRow = within(transcriptTable)
      .getAllByText('NM_007294.4')
      .map((node) => node.closest('tr'))
      .find((row) => row?.querySelector('td')?.textContent === 'NM_007294.4');
    expect(refseqTranscriptRow).toBeTruthy();
    expect(within(transcriptTable).getByText('RefSeq Select')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Protein Atlas' })).toHaveAttribute(
      'href',
      'https://www.proteinatlas.org/ENSG00000012048-BRCA1',
    );
    // Monarch addresses a gene node by its HGNC CURIE, the same way the disease
    // associations link (monarchinitiative.org/MONDO:…), not by a text search.
    expect(screen.getByRole('link', { name: 'Monarch' })).toHaveAttribute(
      'href',
      'https://monarchinitiative.org/HGNC:1100',
    );
    // MGI catalogues the mouse: the ortholog's marker page, not a search for the human
    // symbol. The old searchtool endpoint answers every query with the MGI homepage.
    expect(screen.getByRole('link', { name: 'MGI' })).toHaveAttribute(
      'href',
      'https://www.informatics.jax.org/marker/MGI:104537',
    );
    // The provenance strip names which release of each source produced this page, and
    // which annotation the loci came from — not merely that a source answered.
    expect(screen.getByText(/Annotation gencode v50 \(Ensembl 116\)/)).toBeInTheDocument();
    expect(screen.getByText('dbNSFP 5.4')).toBeInTheDocument();
    expect(screen.getByText('HGNC 2026-08-07')).toBeInTheDocument();
    const footer = document.querySelector('.gene-compact-footer') as HTMLElement;
    expect(within(footer).getByText('ACMG SF v3.3')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'KEGG' })).toHaveAttribute(
      'href',
      'https://www.genome.jp/dbget-bin/www_bget?hsa:672',
    );
    expect(screen.getByText(/latest refresh:/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /refresh gene/i })).not.toBeInTheDocument();
  });

  // #610 — a failed gene profile fell back to "Select a human gene", as if none had been
  // chosen.
  it('says the gene could not be loaded, not that none was chosen, and retries', async () => {
    const get = api.get as unknown as Mock;
    const working = get.getMockImplementation()!;
    let failing = true;
    get.mockImplementation((url: string, config?: unknown) =>
      failing && url === '/genes/profile'
        ? Promise.reject(
            Object.assign(new Error('HTTP 503'), {
              response: { status: 503, data: { detail: 'Gene store unavailable' } },
            }),
          )
        : working(url, config),
    );

    renderPage();

    expect(await screen.findByRole('heading', { name: 'BRCA1 could not be loaded' })).toBeInTheDocument();
    expect(screen.getByText('Gene store unavailable')).toBeInTheDocument();
    expect(screen.queryByText('Select a human gene')).not.toBeInTheDocument();

    failing = false;
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('heading', { name: 'BRCA1' })).toBeInTheDocument();
  });

  // ENG-10: the subsection holding ClinGen's curation counts and the ACMG SF row was titled
  // "GenCC details".
  it('titles the curation counts and the ACMG SF row for what they are', async () => {
    renderPage();
    const row = await screen.findByText('ACMG secondary finding');
    const subsection = row.closest('.gene-compact-subsection') as HTMLElement;
    expect(within(subsection).getByText('ClinGen curations and ACMG SF')).toBeInTheDocument();
    expect(within(subsection).getByText('Dosage sensitivity')).toBeInTheDocument();
    expect(screen.queryByText('GenCC details')).not.toBeInTheDocument();
  });

  // CLIN-19: the row read a key nothing wrote, so it said "—" for every gene, BRCA1 included.
  it('says a gene the ACMG SF list leaves out is not on it, naming the list version', async () => {
    renderGeneFrom('CFTR', (profile) => {
      delete profile.extra.acmg_secondary_finding;
      profile.source_status.acmg_sf = {
        ...profile.source_status.acmg_sf,
        status: 'missing',
        message: 'No ACMG SF list record for CFTR',
        payload: { symbols_with_records: 84, record_count: 0 },
      };
    });

    expect(await screen.findByRole('heading', { name: 'CFTR' })).toBeInTheDocument();
    const row = acmgSecondaryFindingRow();
    expect(row).toHaveTextContent('ACMG secondary findingNot on ACMG SF v3.3');
    expect(row).not.toHaveTextContent('—');
  });

  it('shows the footnotes of the list that narrow what is reported for a gene', async () => {
    renderGeneFrom('TTN', (profile) => {
      profile.extra.acmg_secondary_finding = {
        list: 'ACMG SF',
        version: '3.3',
        gene: 'TTN',
        hgnc_id: 'HGNC:12403',
        phenotype: 'Dilated cardiomyopathy',
        omim_disorders: ['604145'],
        category: 'Cardiovascular Disease Predisposition',
        inheritance: 'AD',
        variants_to_report: 'All P and LP (see text)',
        added_in: '3.0',
        notes: [
          {
            footnote: 'f',
            text:
              'Only truncating variants in the Titin gene (TTNtv) should be reported as a ' +
              'secondary finding.',
          },
        ],
        source_url: 'https://doi.org/10.1016/j.gim.2025.101454',
      };
    });

    expect(await screen.findByRole('heading', { name: 'TTN' })).toBeInTheDocument();
    const row = acmgSecondaryFindingRow();
    expect(
      within(row).getByText(
        'Dilated cardiomyopathy · Cardiovascular Disease Predisposition · AD · All P and LP (see text)',
      ),
    ).toBeInTheDocument();
    expect(
      within(row).getByText(
        'Only truncating variants in the Titin gene (TTNtv) should be reported as a secondary finding.',
      ),
    ).toBeInTheDocument();
  });

  it('never says a gene is not on the list when the list was not read for it', async () => {
    // A gene cached by a gene reference sync from before CoGA had the list.
    renderGeneFrom('CFTR', (profile) => {
      delete profile.extra.acmg_secondary_finding;
      delete profile.source_status.acmg_sf;
    });

    expect(await screen.findByRole('heading', { name: 'CFTR' })).toBeInTheDocument();
    expect(acmgSecondaryFindingRow()).toHaveTextContent(
      'ACMG secondary findingNot checked against the ACMG SF list',
    );
  });

  it('says the ACMG SF list could not be read when the sync could not read it', async () => {
    renderGeneFrom('CFTR', (profile) => {
      delete profile.extra.acmg_secondary_finding;
      profile.source_status.acmg_sf = {
        status: 'error',
        fetched_at: '2026-10-14T09:00:00Z',
        source_url: '/data/ref-data/acmg-sf/acmg-sf-genes.json',
        message:
          'The ACMG SF list at /data/ref-data/acmg-sf/acmg-sf-genes.json could not be read: ' +
          'the list says it holds 84 genes but holds 83',
        release: null,
        release_detail: {},
        payload: {},
      };
    });

    expect(await screen.findByRole('heading', { name: 'CFTR' })).toBeInTheDocument();
    const row = acmgSecondaryFindingRow();
    expect(row).toHaveTextContent(
      'ACMG secondary findingNot checked: the ACMG SF list could not be read',
    );
    expect(within(row).getByTitle(/holds 84 genes but holds 83/)).toBeInTheDocument();
    expect(row).not.toHaveTextContent(/Not on/);
  });
});
