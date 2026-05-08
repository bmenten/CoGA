import { QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';

import FamilyParaphasePage from '../FamilyParaphasePage';
import { createTestQueryClient } from '../../../test/createTestQueryClient';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

vi.mock('../../../lib/reference', () => ({
  useFamilyReference: () => ({
    assemblyName: 'GRCh38',
    assemblyVersion: 'p14',
    projectId: undefined,
    isLoading: false,
  }),
  formatResolvedReferenceLabel: ({
    speciesName,
    assemblyName,
    assemblyVersion,
  }: {
    speciesName?: string;
    assemblyName?: string;
    assemblyVersion?: string;
  }) =>
    [speciesName, assemblyName ? `${assemblyName}${assemblyVersion ? ` ${assemblyVersion}` : ''}` : undefined]
      .filter(Boolean)
      .join(' • ') || 'Not linked',
}));

describe('FamilyParaphasePage', () => {
  it('renders per-sample Paraphase copy-number and phasing summaries', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({
          data: {
            family_id: 'F1',
            members: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
            ],
            projects: [],
          },
        });
      }

      if (url === '/families/F1/paraphase') {
        return Promise.resolve({
          data: {
            samples: [
              { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'male' },
              { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
            ],
            genes: [
              {
                gene_symbol: 'smn1',
                is_medically_relevant: true,
                region_info: {
                  region_id: 'smn1_smn2',
                  display_name: 'SMN1/SMN2',
                  genes: ['SMN1', 'SMN2'],
                  summary: 'Clinically relevant copy-number calls for SMN1 and SMN2.',
                  clinical_priority: 1,
                  key_copy_number_fields: ['smn1_cn', 'smn2_cn', 'smn_del78_cn'],
                  key_read_fields: ['smn1_read_number', 'smn2_read_number'],
                  key_haplotype_fields: ['smn1_haplotypes', 'smn2_haplotypes'],
                  key_extra_fields: [],
                  field_descriptions: {},
                  notes: [
                    'A null SMN1 or SMN2 copy-number call can occur when depth is ambiguous.',
                  ],
                  disorders: [
                    {
                      name: 'Spinal muscular atrophy',
                      omim_url: 'https://www.omim.org/entry/253300',
                    },
                  ],
                },
                max_total_cn: null,
                max_gene_cn: 2,
                max_highest_total_cn: 4,
                has_copy_number_signal: true,
                samples: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    total_cn: null,
                    gene_cn: null,
                    highest_total_cn: 4,
                    sample_sex: 'male',
                    phase_region: '38:chr5:70917100-70961220',
                    region_depth: { median: 65, percentile80: 70 },
                    genome_depth: 33,
                    final_haplotype_count: 6,
                    assembled_haplotype_count: 6,
                    variant_site_count: 12,
                    heterozygous_site_count: 10,
                    fusion_count: null,
                    copy_number_signal: true,
                    copy_number_metrics: [
                      { key: 'smn1_cn', label: 'SMN1 CN', value: null },
                      { key: 'smn2_cn', label: 'SMN2 CN', value: 3 },
                      { key: 'smn_del78_cn', label: 'SMNΔ7-8 CN', value: 0 },
                    ],
                    read_metrics: [
                      { key: 'smn1_read_number', label: 'SMN1 reads c.840C', value: 14 },
                      { key: 'smn2_read_number', label: 'SMN2 reads c.840T', value: 24 },
                    ],
                    extra_fields: [],
                    haplotype_groups: [
                      {
                        key: 'assembled_haplotypes',
                        label: 'Assembled haplotypes',
                        count: 6,
                        haplotypes: ['assembled_1', 'assembled_2'],
                      },
                      {
                        key: 'smn1_haplotypes',
                        label: 'SMN1 haplotypes',
                        count: 1,
                        haplotypes: ['smn1_smn1hap1'],
                      },
                      {
                        key: 'smn2_haplotypes',
                        label: 'SMN2 haplotypes',
                        count: 3,
                        haplotypes: ['smn1_smn2hap1', 'smn1_smn2hap2', 'smn1_smn2hap3'],
                      },
                    ],
                  },
                  MOTHER: {
                    sample: 'MOTHER',
                    role: 'mother',
                    affected: false,
                    sex: 'female',
                    total_cn: 2,
                    gene_cn: 2,
                    highest_total_cn: 2,
                    sample_sex: 'female',
                    phase_region: null,
                    region_depth: {},
                    genome_depth: 31,
                    final_haplotype_count: 2,
                    assembled_haplotype_count: 2,
                    variant_site_count: 8,
                    heterozygous_site_count: 6,
                    fusion_count: null,
                    copy_number_signal: false,
                    copy_number_metrics: [
                      { key: 'smn1_cn', label: 'SMN1 CN', value: 2 },
                      { key: 'smn2_cn', label: 'SMN2 CN', value: 2 },
                    ],
                    read_metrics: [],
                    extra_fields: [],
                    haplotype_groups: [],
                  },
                },
              },
              {
                gene_symbol: 'RCCX',
                is_medically_relevant: true,
                region_info: {
                  region_id: 'rccx',
                  display_name: 'RCCX module',
                  genes: ['RCCX', 'CYP21A2', 'TNXB', 'C4A', 'C4B'],
                  summary: 'Complex RCCX structural variation.',
                  clinical_priority: 2,
                  key_copy_number_fields: ['total_cn', 'gene_cn', 'highest_total_cn'],
                  key_read_fields: [],
                  key_haplotype_fields: ['final_haplotypes'],
                  key_extra_fields: ['annotated_alleles', 'hap_variants'],
                  field_descriptions: {
                    annotated_alleles: 'Per-allele CYP21A2 annotations.',
                  },
                  notes: [],
                  disorders: [
                    {
                      name: '21-hydroxylase-deficient congenital adrenal hyperplasia',
                      omim_url: 'https://www.omim.org/entry/201910',
                    },
                  ],
                },
                max_total_cn: 4,
                max_gene_cn: 2,
                max_highest_total_cn: 4,
                has_copy_number_signal: true,
                samples: {
                  PROBAND: {
                    sample: 'PROBAND',
                    role: 'proband',
                    affected: true,
                    sex: 'male',
                    total_cn: 4,
                    gene_cn: 2,
                    highest_total_cn: 4,
                    sample_sex: 'male',
                    phase_region: '38:chr6:32000000-32100000',
                    region_depth: {},
                    genome_depth: 33,
                    final_haplotype_count: 4,
                    assembled_haplotype_count: 4,
                    variant_site_count: 20,
                    heterozygous_site_count: 9,
                    fusion_count: null,
                    copy_number_signal: true,
                    copy_number_metrics: [
                      { key: 'total_cn', label: 'Total CN', value: 4 },
                      { key: 'gene_cn', label: 'Gene CN', value: 2 },
                    ],
                    read_metrics: [],
                    extra_fields: [
                      {
                        key: 'annotated_alleles',
                        label: 'Annotated Alleles',
                        value: ['WT', 'deletion_P31L,G111Vfs'],
                        description: 'Per-allele CYP21A2 annotations.',
                      },
                      {
                        key: 'hap_variants',
                        label: 'Hap Variants',
                        value: { hap1: ['P31L'], hap2: ['Q319X'] },
                      },
                    ],
                    haplotype_groups: [],
                  },
                },
              },
              {
                gene_symbol: 'GBA',
                is_medically_relevant: true,
                region_info: {
                  region_id: 'gba',
                  display_name: 'GBA',
                  genes: ['GBA'],
                  summary: 'GBA copy-number and recombinant allele calls.',
                  clinical_priority: 12,
                  key_copy_number_fields: ['total_cn', 'gene_cn', 'highest_total_cn'],
                  key_read_fields: [],
                  key_haplotype_fields: ['final_haplotypes'],
                  key_extra_fields: [],
                  field_descriptions: {},
                  notes: [],
                  disorders: [
                    {
                      name: 'Gaucher disease',
                      omim_url: 'https://www.omim.org/entry/230800',
                    },
                  ],
                },
                max_total_cn: 2,
                max_gene_cn: 2,
                max_highest_total_cn: 2,
                has_copy_number_signal: false,
                samples: {},
              },
              {
                gene_symbol: 'EXPLORATORY',
                is_medically_relevant: false,
                region_info: null,
                max_total_cn: 2,
                max_gene_cn: 2,
                max_highest_total_cn: 2,
                has_copy_number_signal: false,
                samples: {},
              },
            ],
          },
        });
      }

      return Promise.resolve({ data: {} });
    });

    const queryClient = createTestQueryClient();

    render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/families/F1/paraphase']}>
          <Routes>
            <Route path="/families/:familyId/paraphase" element={<FamilyParaphasePage />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /duplicated-region results/i })).toBeInTheDocument();
    });

    const row = screen.getByText('SMN1/SMN2').closest('tr');
    expect(row).not.toBeNull();
    const rowScope = within(row as HTMLElement);
    expect(rowScope.getByText('Clinical')).toBeInTheDocument();
    expect(rowScope.getByText('Spinal muscular atrophy')).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/253300',
    );
    expect(rowScope.getByText('PROBAND')).toBeInTheDocument();
    expect(rowScope.getByText('MOTHER')).toBeInTheDocument();
    expect(rowScope.getByText(/6 final haplotypes/i)).toBeInTheDocument();
    expect(rowScope.getByText(/38:chr5:70917100-70961220/i)).toBeInTheDocument();
    expect(rowScope.getAllByText('SMN1 CN').length).toBeGreaterThan(0);
    expect(rowScope.getAllByText('SMN2 CN').length).toBeGreaterThan(0);
    expect(rowScope.getByText('SMN1 reads c.840C')).toBeInTheDocument();
    expect(rowScope.getByText('SMN1 haplotypes')).toBeInTheDocument();
    expect(rowScope.getByText('smn1_smn1hap1')).toBeInTheDocument();
    expect(rowScope.queryByText('Assembled haplotypes')).not.toBeInTheDocument();
    expect(rowScope.queryByText('assembled_1, assembled_2')).not.toBeInTheDocument();
    expect(rowScope.getByText(/depth is ambiguous/i)).toBeInTheDocument();
    expect(rowScope.getAllByText('1').length).toBeGreaterThan(0);
    expect(rowScope.getAllByText('2').length).toBeGreaterThan(0);

    const rccxRow = screen.getByText('RCCX module').closest('tr');
    expect(rccxRow).not.toBeNull();
    const rccxScope = within(rccxRow as HTMLElement);
    expect(rccxScope.getByText('Annotated Alleles')).toBeInTheDocument();
    expect(rccxScope.getByText(/WT, deletion_P31L/i)).toBeInTheDocument();
    expect(rccxScope.getByText(/Per-allele CYP21A2 annotations/i)).toBeInTheDocument();
    expect(rccxScope.getByText(/hap1: P31L/i)).toBeInTheDocument();

    expect(screen.getByText('3 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.queryByText('EXPLORATORY')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show all/i }));
    expect(screen.getByText('4 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.getByText('EXPLORATORY')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/copy-number changes only/i));
    expect(screen.getByText('2 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.getByText('SMN1/SMN2')).toBeInTheDocument();
    expect(screen.getByText('RCCX module')).toBeInTheDocument();
    expect(screen.queryByText('GBA')).not.toBeInTheDocument();
    expect(screen.queryByText('EXPLORATORY')).not.toBeInTheDocument();
  });
});
