import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';

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

describe('GeneInfoPage', () => {
  beforeEach(() => {
    vi.resetAllMocks();
    localStorage.clear();
    localStorage.setItem('role', 'admin');

    (api.get as unknown as ReturnType<typeof vi.fn>).mockImplementation(
      (url: string, config?: any) => {
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
                  source: 'ensembl',
                },
                {
                  transcript_id: 'NM_007294.4',
                  start: 43044295,
                  end: 43125482,
                  exon_count: 24,
                  strand: -1,
                  biotype: 'protein_coding',
                  source: 'refgene',
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
                  source_url: '/data/ref-data/dbNSFP5.3_gene.gz',
                  message: null,
                  payload: { record_count: 1 },
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
                ensembl_canonical_transcript: 'ENST00000357654',
                ensembl_description: 'BRCA1 DNA repair associated',
                ncbi_other_designations: ['breast cancer type 1 susceptibility protein'],
                hgnc_vega_id: 'OTTHUMG00000157426',
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
                  {
                    haploinsufficiency: '3',
                    description: 'Sufficient evidence for dosage pathogenicity',
                    disease: 'BRCA1-related cancer predisposition',
                    pmids: ['32375709'],
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
                  clinical_actionability: 2,
                  variant_pathogenicity: 27,
                  cpic_pharmgkb: '6',
                },
                clingen_gene_facts: {
                  hgnc_name: 'BRCA1 DNA repair associated',
                  gene_type: 'protein coding',
                  locus_type: 'gene with protein product',
                  previous_symbols: ['RNF53'],
                  alias_symbols: ['BRCC1'],
                  gencc_classifications: {
                    Definitive: 5,
                    Strong: 1,
                  },
                  haploinsufficiency_index: 13.4,
                  pli: 1.0,
                  loeuf: 0.16,
                  acmg_secondary_finding: true,
                  cytoband: '17q21.31',
                  mane_select_transcript: 'ENST00000357654.9',
                  function: 'DNA repair and double-strand break signaling.',
                  genomic_coordinates: {
                    'GRCh37/hg19': 'chr17:41196311-41277499',
                    'GRCh38/hg38': 'chr17:43044294-43125482',
                  },
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
    expect(screen.getByText('OTTHUMG00000157426')).toBeInTheDocument();
    expect(screen.getByText('NM_007294.4')).toBeInTheDocument();
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
    expect(within(transcriptTable).getByText('MANE Select')).toBeInTheDocument();
    expect(within(transcriptTable).getByText('Ensembl Canonical')).toBeInTheDocument();
    // RefSeq Select is derived from the HGNC refseq accessions.
    expect(within(transcriptTable).getByText('NM_007294.4')).toBeInTheDocument();
    expect(within(transcriptTable).getByText('RefSeq Select')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Protein Atlas' })).toHaveAttribute(
      'href',
      'https://www.proteinatlas.org/ENSG00000012048-BRCA1',
    );
    expect(screen.getByRole('link', { name: 'Monarch' })).toHaveAttribute(
      'href',
      'https://monarchinitiative.org/search?q=BRCA1',
    );
    expect(screen.getByRole('link', { name: 'KEGG' })).toHaveAttribute(
      'href',
      'https://www.genome.jp/dbget-bin/www_bget?hsa:672',
    );
    expect(screen.getByText(/latest refresh:/i)).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /refresh gene/i })).not.toBeInTheDocument();
  });
});
