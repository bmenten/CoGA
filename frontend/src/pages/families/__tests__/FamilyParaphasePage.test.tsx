import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import FamilyParaphasePage from '../FamilyParaphasePage';
import { renderFamilyRoute } from '../../../test/familyRoute';
import {
  expectFailedRequestRetried,
  expectFamilyNotFound,
  familyNotFound,
  serverError,
} from '../../../test/requestFailures';

const apiMock = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock('../../../lib/api', () => ({
  default: apiMock,
}));

vi.mock('../../../lib/reference', () => ({
  // The family header lists the family's projects by name.
  useProjectCatalog: () => ({ data: [] }),
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

/** The page of family F1, at `url`. */
const renderPage = (url = '/families/F1/paraphase') =>
  renderFamilyRoute('/families/:familyId/paraphase', url, <FamilyParaphasePage />);

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
                    clinical_status: 'no_call',
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
                    clinical_status: 'normal',
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
                  genes: ['CYP21A2', 'TNXB', 'C4A', 'C4B'],
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
                    clinical_status: 'pathogenic',
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
                max_total_cn: 4,
                max_gene_cn: null,
                max_highest_total_cn: 4,
                has_copy_number_signal: false,
                samples: {
                  // A normal GBA + GBAP1 pair: total CN 4, gene CN not called. The
                  // backend calls it unchanged, and the page does not re-flag a value
                  // other than 2 (a no-call included), so the filter drops it.
                  MOTHER: {
                    sample: 'MOTHER',
                    role: 'mother',
                    affected: false,
                    sex: 'female',
                    clinical_status: 'none',
                    total_cn: 4,
                    gene_cn: null,
                    highest_total_cn: 4,
                    sample_sex: 'female',
                    phase_region: null,
                    region_depth: {},
                    genome_depth: 31,
                    final_haplotype_count: 4,
                    assembled_haplotype_count: 4,
                    variant_site_count: 8,
                    heterozygous_site_count: 6,
                    fusion_count: null,
                    copy_number_signal: false,
                    copy_number_metrics: [
                      { key: 'total_cn', label: 'Total CN', value: 4 },
                      { key: 'gene_cn', label: 'Gene CN', value: null },
                      { key: 'highest_total_cn', label: 'Highest total CN', value: 4 },
                    ],
                    read_metrics: [],
                    extra_fields: [],
                    haplotype_groups: [],
                  },
                },
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

      return Promise.resolve({ data: [] });
    });

    renderPage();

    await waitFor(() => {
      expect(screen.getByRole('heading', { name: /duplicated-region results/i })).toBeInTheDocument();
    });
    // The filter is ticked when the page opens; this test starts from every region.
    const changesOnly = screen.getByLabelText(/copy-number changes and abnormal calls only/i);
    expect(changesOnly).toBeChecked();
    fireEvent.click(changesOnly);

    const smnCard = screen.getByRole('article', { name: 'SMN1/SMN2 region' });
    expect(smnCard).not.toBeNull();
    const smnScope = within(smnCard as HTMLElement);
    expect(smnScope.getByText('Clinical')).toBeInTheDocument();
    // Gene first, to its gene page, then the disease, to its OMIM entry: every card's heading.
    // A region named after its genes links each of them.
    expect(smnScope.getByText('Spinal muscular atrophy').closest('.gene-disease-heading')).toHaveTextContent(
      'SMN1/SMN2—Spinal muscular atrophy',
    );
    expect(smnScope.getByRole('link', { name: 'SMN1' })).toHaveAttribute('href', '/genes?gene=SMN1&family_id=F1');
    expect(smnScope.getByRole('link', { name: 'SMN2' })).toHaveAttribute('href', '/genes?gene=SMN2&family_id=F1');
    expect(smnScope.getByRole('link', { name: /spinal muscular atrophy on omim/i })).toHaveAttribute(
      'href',
      'https://www.omim.org/entry/253300',
    );
    // The shared family header: the title is the way back to the workspace, and the
    // page carries no separate button to the same place.
    expect(screen.getByRole('link', { name: 'Family F1' })).toHaveAttribute(
      'href',
      '/families/F1',
    );

    // Chromosome-view link is derived from the analysed phase_region and uses the
    // shared red small-variant link style. Both genome links leave this table behind,
    // so both open a tab — as on the small-variant, SV and repeat tables.
    const smnChromLink = smnScope.getByRole('link', {
      name: /chromosome view at chr5:70,917,100-70,961,220.*opens in a new tab/i,
    });
    expect(smnChromLink).toHaveAttribute(
      'href',
      '/families/F1/chromosome/5?start=70917100&end=70961220',
    );
    expect(smnChromLink).toHaveClass('variant-card-resource--clinical');
    expect(smnChromLink).toHaveAttribute('target', '_blank');
    expect(smnChromLink).toHaveAttribute('rel', 'noopener noreferrer');

    // IGV opens on the analysed region plus 1 kb, so its edges are visible.
    const smnIgvLink = smnScope.getByRole('link', {
      name: /open igv at chr5:70916100-70962220.*opens in a new tab/i,
    });
    expect(smnIgvLink).toHaveAttribute(
      'href',
      '/families/F1/igv?locus=chr5%3A70916100-70962220' +
        '&back_path=%2Ffamilies%2FF1%2Fparaphase',
    );
    expect(smnIgvLink).toHaveAttribute('target', '_blank');
    expect(smnScope.getByText('PROBAND')).toBeInTheDocument();
    expect(smnScope.getByText('MOTHER')).toBeInTheDocument();
    // Pathogenicity status chips + key CN are visible by default.
    expect(smnScope.getByText('No-call')).toBeInTheDocument();
    expect(smnScope.getByText('Normal')).toBeInTheDocument();
    expect(smnScope.getAllByText('SMN1 CN').length).toBeGreaterThan(0);
    expect(smnScope.getAllByText('SMN2 CN').length).toBeGreaterThan(0);
    // Verbose technical detail is hidden until "Extra info" is opened.
    expect(smnScope.queryByText(/6 final haplotypes/i)).not.toBeInTheDocument();
    fireEvent.click(smnScope.getByRole('button', { name: /^extra info$/i }));
    expect(smnScope.getByText(/6 final haplotypes/i)).toBeInTheDocument();
    expect(smnScope.getByText(/38:chr5:70917100-70961220/i)).toBeInTheDocument();
    expect(smnScope.getByText('SMN1 reads c.840C')).toBeInTheDocument();
    expect(smnScope.getByText('SMN1 haplotypes')).toBeInTheDocument();
    expect(smnScope.getByText('smn1_smn1hap1')).toBeInTheDocument();
    expect(smnScope.queryByText('Assembled haplotypes')).not.toBeInTheDocument();
    expect(smnScope.queryByText('assembled_1, assembled_2')).not.toBeInTheDocument();
    expect(smnScope.getByText(/depth is ambiguous/i)).toBeInTheDocument();

    const rccxCard = screen.getByRole('article', { name: 'RCCX module region' });
    // A pathogenic sample gives the locus card the red top+left rail.
    expect(rccxCard).toHaveClass('variant-card--clinvar-pathogenic');
    const rccxScope = within(rccxCard as HTMLElement);
    expect(rccxScope.getByText('Pathogenic')).toBeInTheDocument();
    // RCCX names no gene: the title stands unlinked, and each of its genes leads to its page.
    expect(rccxScope.queryByRole('link', { name: 'RCCX module' })).not.toBeInTheDocument();
    expect(
      ['CYP21A2', 'TNXB', 'C4A', 'C4B'].map((symbol) =>
        rccxScope.getByRole('link', { name: symbol }).getAttribute('href'),
      ),
    ).toEqual([
      '/genes?gene=CYP21A2&family_id=F1',
      '/genes?gene=TNXB&family_id=F1',
      '/genes?gene=C4A&family_id=F1',
      '/genes?gene=C4B&family_id=F1',
    ]);
    fireEvent.click(rccxScope.getByRole('button', { name: /^extra info$/i }));
    expect(rccxScope.getByText('Annotated Alleles')).toBeInTheDocument();
    // One allele per line: a label holds commas of its own.
    expect(rccxScope.getByText('WT').closest('.paraphase-extra-list')).toHaveTextContent('WTdeletion_P31L,G111Vfs');
    expect(rccxScope.getByText('deletion_P31L,G111Vfs')).toBeInTheDocument();
    expect(rccxScope.getByText(/Per-allele CYP21A2 annotations/i)).toBeInTheDocument();
    expect(rccxScope.getByText(/hap1: P31L/i)).toBeInTheDocument();

    expect(screen.getByText('3 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.queryByText('EXPLORATORY')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /show all/i }));
    expect(screen.getByText('4 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.getByText('EXPLORATORY')).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText(/copy-number changes and abnormal calls only/i));
    expect(screen.getByText('2 of 4 regions · 3 clinical · 2 with CN signals')).toBeInTheDocument();
    expect(screen.getByRole('article', { name: 'SMN1/SMN2 region' })).toBeInTheDocument();
    expect(screen.getByText('RCCX module')).toBeInTheDocument();
    expect(screen.queryByText('GBA')).not.toBeInTheDocument();
    expect(screen.queryByText('EXPLORATORY')).not.toBeInTheDocument();
  });
});

// A failure is said as such: a server error never reads "Family not found".
describe('FamilyParaphasePage — failed requests', () => {

  it('says the family could not be loaded, not that it was not found', async () => {
    let failedRequests = 0;
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        failedRequests += 1;
        return serverError();
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();

    await expectFailedRequestRetried({
      heading: 'Family could not be loaded',
      message: 'Request failed with status code 500',
      requests: () => failedRequests,
    });
  });

  it('keeps "Family not found" for a family the API does not know', async () => {
    apiMock.get.mockImplementation(familyNotFound);
    renderPage();

    await expectFamilyNotFound();
  });
});

// How to read a region opens on request, under the card: explanation, the same for every
// family. This family's calls, their status and the region's caveat stay on the card.
describe('FamilyParaphasePage — the region card', () => {
  const members = [
    { sample_id: 'PROBAND', role: 'proband', affected: true, sex: 'female' },
    { sample_id: 'MOTHER', role: 'mother', affected: false, sex: 'female' },
  ];
  const call = (sample: string, status: string, cn: number) => ({
    sample,
    role: sample === 'PROBAND' ? 'proband' : 'mother',
    clinical_status: status,
    phase_region: '7:chr5:70917100-70961220',
    region_depth: {},
    final_haplotype_count: 2,
    assembled_haplotype_count: 2,
    variant_site_count: 3,
    heterozygous_site_count: 1,
    copy_number_metrics: [{ key: 'smn1_cn', label: 'SMN1 CN', value: cn }],
    read_metrics: [],
  });
  const gene = {
    gene_symbol: 'smn1',
    is_medically_relevant: true,
    has_copy_number_signal: true,
    region_info: {
      region_id: 'smn1_smn2',
      display_name: 'SMN1/SMN2',
      genes: ['SMN1', 'SMN2'],
      summary: 'Copy-number calls for SMN1 and SMN2.',
      clinical: {
        interpretation: 'Read SMN1 copy number first: 0 copies = affected, 1 = carrier.',
        normal: 'SMN1 CN 2.',
        caveats: 'Misses silent 2+0 carriers.',
      },
      clinical_priority: 1,
      key_copy_number_fields: ['smn1_cn'],
      key_read_fields: [],
      key_haplotype_fields: [],
      key_extra_fields: [],
      field_descriptions: { smn1_cn: 'Copies of SMN1.' },
      notes: [],
      disorders: [
        { name: 'Spinal muscular atrophy', omim_url: 'https://www.omim.org/entry/253300' },
        { name: 'A disorder without an entry', omim_url: null },
      ],
      guide: [],
    },
    samples: { PROBAND: call('PROBAND', 'carrier', 1), MOTHER: call('MOTHER', 'normal', 2) },
  };

  // Ticked when the page opens, the filter keeps a region with a copy-number change or a call
  // that reads abnormal: a fusion is reviewed without any change in copy number.
  it('opens on the regions with a copy-number change or an abnormal call, a fusion review included', async () => {
    const region = (gene_symbol: string, status: string, signal: boolean) => ({
      ...gene,
      gene_symbol,
      has_copy_number_signal: signal,
      region_info: { ...gene.region_info, region_id: gene_symbol, display_name: gene_symbol, genes: [gene_symbol] },
      samples: {
        PROBAND: { ...call('PROBAND', status, 2), copy_number_signal: signal },
        MOTHER: { ...call('MOTHER', 'normal', 2), copy_number_signal: false },
      },
    });
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/paraphase') {
        return Promise.resolve({
          data: {
            samples: members,
            genes: [region('CNCHANGE', 'carrier', true), region('FUSION', 'review', false), region('QUIET', 'normal', false)],
          },
        });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();
    expect(await screen.findByRole('article', { name: /CNCHANGE/ })).toBeInTheDocument();
    expect(screen.getByLabelText(/copy-number changes and abnormal calls only/i)).toBeChecked();
    expect(screen.getByRole('article', { name: /FUSION/ })).toBeInTheDocument();
    expect(screen.queryByRole('article', { name: /QUIET/ })).not.toBeInTheDocument();

    fireEvent.click(screen.getByLabelText(/copy-number changes and abnormal calls only/i));
    expect(screen.getByRole('article', { name: /QUIET/ })).toBeInTheDocument();
  });

  it('keeps the calls and the caveat visible and how to read the region behind its toggle', async () => {
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/paraphase') {
        return Promise.resolve({ data: { samples: members, genes: [gene] } });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();
    const card = await screen.findByRole('article', { name: 'SMN1/SMN2 region' });
    const scope = within(card);

    // Proband first, each with their copy number and status, in the genotype grid every
    // variant card has.
    const grid = card.querySelector('.variant-card-gtlist') as HTMLElement;
    expect(grid.querySelector('.variant-card-gthead')).toHaveTextContent('SampleRoleSMN1 CNStatus');
    const rows = grid.querySelectorAll('.variant-card-gtline');
    expect(rows[0]).toHaveTextContent('PROBANDproband1Carrier');
    expect(rows[0]).toHaveClass('variant-card-gtline--warn');
    expect(rows[1]).toHaveTextContent('MOTHERmother2Normal');

    // The caveat's mark stays on the card; its full text is in the tooltip alone.
    expect(scope.queryByText('Misses silent 2+0 carriers.')).not.toBeInTheDocument();
    const caveat = scope.getByText('Read before interpreting');
    fireEvent.focus(caveat);
    expect(screen.getByRole('tooltip')).toHaveTextContent('Misses silent 2+0 carriers.');
    fireEvent.blur(caveat);
    expect(scope.queryByText(/read smn1 copy number first/i)).not.toBeInTheDocument();
    const toggle = scope.getByRole('button', { name: 'How to read' });
    expect(toggle).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(toggle);
    expect(scope.getByRole('button', { name: 'Hide how to read' })).toHaveAttribute('aria-expanded', 'true');
    const guide = scope.getByRole('region', { name: 'How to read SMN1/SMN2' });
    expect(within(guide).getByText('Copy-number calls for SMN1 and SMN2.')).toBeInTheDocument();
    expect(within(guide).getByText(/read smn1 copy number first/i)).toBeInTheDocument();
    // What each call means, the caveat in full, and the columns of the genotype grid.
    expect(within(guide).getByText('Normal').nextElementSibling).toHaveTextContent('SMN1 CN 2.');
    expect(within(guide).queryByText('Carrier')).not.toBeInTheDocument();
    expect(within(guide).getByText('Misses silent 2+0 carriers.')).toBeInTheDocument();
    expect(within(guide).getByText('SMN1 CN').nextElementSibling).toHaveTextContent('Copies of SMN1.');
    fireEvent.click(scope.getByRole('button', { name: 'Hide how to read' }));
    expect(scope.queryByRole('region', { name: 'How to read SMN1/SMN2' })).not.toBeInTheDocument();

    // A disorder without an OMIM URL is named without a link, never one to "#".
    expect(scope.getByText('A disorder without an entry')).toBeInTheDocument();
    expect(scope.getAllByRole('link', { name: /on omim/i })).toHaveLength(1);
    // The footer every card has: the extra-info toggle at the left, the links at the right
    // in one order.
    const footer = card.querySelector('.variant-card-footer') as HTMLElement;
    expect(within(footer).getByRole('button', { name: 'How to read' }).closest('.variant-card-quick')).not.toBeNull();
    expect(within(footer).getByRole('button', { name: 'Extra info' }).closest('.variant-card-quick')).not.toBeNull();
    const nav = footer.querySelector('.variant-card-nav') as HTMLElement;
    expect(within(nav).getAllByRole('link').map((link) => link.textContent)).toEqual([
      'IGV',
      'Chromosome view',
      'OMIM',
    ]);
  });
  it("shows the catalogue's guide of a region: its sections, their terms and the extra fields explained", async () => {
    const rccx = {
      gene_symbol: 'RCCX',
      is_medically_relevant: true,
      has_copy_number_signal: true,
      region_info: {
        region_id: 'rccx',
        display_name: 'RCCX module',
        genes: ['CYP21A2', 'TNXB', 'C4A', 'C4B'],
        summary: 'RCCX structural variation.',
        clinical: { interpretation: 'Read the alleles, not the copy number.' },
        clinical_priority: 2,
        key_copy_number_fields: ['total_cn'],
        key_read_fields: [],
        key_haplotype_fields: [],
        key_extra_fields: ['annotated_alleles', 'phasing_success'],
        field_descriptions: {
          total_cn: 'RCCX modules on both chromosomes.',
          annotated_alleles: 'One label per chromosome.',
          phasing_success: 'Whether the copies join into alleles.',
        },
        notes: [],
        disorders: [],
        guide: [
          { heading: 'The module', text: 'Most chromosomes carry two modules.', terms: [] },
          {
            heading: 'Allele labels',
            text: null,
            terms: [
              { term: 'WT', text: 'A normal allele.' },
              { term: 'gene_deletion', text: 'A damaging allele.' },
            ],
          },
        ],
      },
      samples: {
        PROBAND: {
          ...call('PROBAND', 'review', 3),
          copy_number_metrics: [{ key: 'total_cn', label: 'Total CN', value: 3 }],
          extra_fields: [{ key: 'annotated_alleles', label: 'Annotated Alleles', value: ['WT', 'gene_deletion'] }],
        },
      },
    };
    const exploratory = {
      gene_symbol: 'EXPLORATORY',
      is_medically_relevant: false,
      has_copy_number_signal: true,
      region_info: null,
      samples: { PROBAND: { ...call('PROBAND', 'review', 3), copy_number_metrics: [] } },
    };
    apiMock.get.mockImplementation((url: string) => {
      if (url === '/families/F1') {
        return Promise.resolve({ data: { family_id: 'F1', members, projects: [] } });
      }
      if (url === '/families/F1/paraphase') {
        return Promise.resolve({ data: { samples: members, genes: [rccx, exploratory] } });
      }
      return Promise.resolve({ data: [] });
    });
    renderPage();
    const card = await screen.findByRole('article', { name: 'RCCX module region' });
    fireEvent.click(within(card).getByRole('button', { name: 'How to read' }));
    const guide = within(within(card).getByRole('region', { name: 'How to read RCCX module' }));

    expect(guide.getByText('Read the alleles, not the copy number.')).toBeInTheDocument();
    expect(guide.getByRole('heading', { name: 'The module' })).toBeInTheDocument();
    expect(guide.getByText('Most chromosomes carry two modules.')).toBeInTheDocument();
    expect(guide.getByRole('heading', { name: 'Allele labels' })).toBeInTheDocument();
    expect(guide.getByText('gene_deletion').nextElementSibling).toHaveTextContent('A damaging allele.');
    // The column by its name in the genotype grid; an extra field by its name under Extra info,
    // or its key in words where no member's call has it.
    expect(guide.getByText('Total CN').nextElementSibling).toHaveTextContent('RCCX modules on both chromosomes.');
    expect(guide.getByText('Annotated Alleles').nextElementSibling).toHaveTextContent('One label per chromosome.');
    expect(guide.getByText('phasing success').nextElementSibling).toHaveTextContent(
      'Whether the copies join into alleles.',
    );

    // A region outside the catalogue has nothing to explain: no toggle.
    fireEvent.click(screen.getByRole('button', { name: /show all/i }));
    const other = await screen.findByRole('article', { name: 'EXPLORATORY region' });
    expect(within(other).queryByRole('button', { name: 'How to read' })).not.toBeInTheDocument();
    expect(within(other).getByRole('button', { name: 'Extra info' })).toBeInTheDocument();
  });
});
