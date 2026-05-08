import { QueryClientProvider } from '@tanstack/react-query';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
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
                  transcript_id: 'TX1',
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
                constraint_metrics: {
                  missense_z: 3.21,
                  shet: 0.094,
                  phaplo: 0.88,
                  ptriplo: 0.06,
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
    expect(screen.getAllByText(/gene-disease validity/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/DECIPHER %HI/i)).toBeInTheDocument();
    expect(screen.getByText('OTTHUMG00000157426')).toBeInTheDocument();
    expect(screen.getByText('NM_007294.4')).toBeInTheDocument();
    expect(screen.getByText('3.21')).toBeInTheDocument();
    expect(screen.getByText('0.094')).toBeInTheDocument();
    expect(screen.getByText('0.880')).toBeInTheDocument();
    expect(screen.getByText('0.060')).toBeInTheDocument();
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
