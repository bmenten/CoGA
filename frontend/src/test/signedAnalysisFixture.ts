// What the analysis looked at, as a signed record freezes it since 2026-10-14
// (backend/app/services/signed_record_sections.py): shared by the tests of the record's
// reading (signedReportRecord.test.ts) and of its signed view (FamilyReportPage.test.tsx).

export const SIGNED_STATEMENT =
  'This record is the analytical sign-off in CoGA, which closes and freezes the analysis; the medical sign-off of the patient report happens in the EPD, which refers to this record by its report code.';

export const SIGNED_ANALYSIS = {
  record_statement: SIGNED_STATEMENT,
  variant_descriptions: {
    small: [
      {
        variant_id: '17-43000000-A-G',
        source: 'clair3',
        chrom: '17',
        start: 43000000,
        end: 43000001,
        ref: 'A',
        alt: 'G',
        gene: 'GENE_PATH',
        gene_id: 'ENSG01',
        transcript: 'ENST01',
        hgvsc: 'c.10A>G',
        hgvsp: 'p.(Lys4Arg)',
        consequence: 'missense_variant',
        impact: 'MODERATE',
        mane_select: true,
        canonical: true,
        genotypes: [
          { sample: 'MOTHER', gt: '0/0', dp: 28, gq: 60, ad: null, af: null },
          { sample: 'PROBAND', gt: '0/1', dp: 30, gq: null, ad: [15, 15], af: [0.5] },
        ],
      },
      'not an entry',
    ],
    structural: [
      {
        variant_id: '2-1000-50000-DEL---',
        source: 'needlr',
        sv_type: 'DEL',
        copy_change: 'loss',
        chrom: '2',
        start: 1000,
        end: 50000,
        length: 49000,
        remote_chrom: null,
        remote_start: null,
        genes: ['GENE_DEL'],
        cytoband: '2p25.3',
        genotypes: [{ sample: 'PROBAND', gt: '0/1', cn: 1, read_support: 18, qual: 60, filter: null }],
      },
    ],
  },
  gene_panels: {
    applied: [
      {
        panel_id: 'p1',
        name: 'Epilepsy',
        version: 2,
        source: 'panelapp',
        gene_count: 120,
        external_id: '402',
        external_version: '3.1',
        external_version_created: '2026-08-01',
        confidence_levels: ['3'],
        source_updated_at: '2026-09-01T08:00:00+00:00',
        first_applied_at: '2026-10-10T09:00:00+00:00',
        first_applied_by: 'analyst',
        first_search: 'small_variants',
      },
    ],
  },
  reference_data: {
    gene_reference: {
      genes: ['GENE_DEL', 'GENE_PATH', 'NOT_LOADED'],
      sources: [
        { source: 'clingen_dosage', status: 'success', release: '2026-09-30', checksum: 'dd', genes: ['GENE_DEL'] },
        {
          source: 'hgnc_complete_set',
          status: 'success',
          release: '2026-09-01',
          checksum: 'aa',
          genes: ['GENE_DEL', 'GENE_PATH'],
        },
        { source: 'clingen_dosage', status: 'missing', release: '2026-08-30', checksum: 'cc', genes: ['GENE_PATH'] },
      ],
      not_in_reference: ['NOT_LOADED'],
    },
    clinical_cnvs: {
      source: 'clinical-cnv-kb',
      release: 'not stated',
      release_date: null,
      source_url: null,
      loaded_at: '2026-10-01T09:00:00+00:00',
      records: 1234,
    },
  },
  repeat_expansions: {
    catalogue: { loci: 96, sources: ['CoGA built-in', 'STRchive'], digest: 'abcdef0123456789' },
    loci: [
      {
        locus_id: 'HD_HTT',
        gene: 'HTT',
        disease: 'Huntington disease',
        inheritance: 'AD',
        chrom: 'chr4',
        start: 3074876,
        end: 3074933,
        motif: 'CAG',
        thresholds: {
          intermediate_min: 27,
          premutation_min: null,
          pathogenic_min: 36,
          benign_min: null,
          benign_max: null,
          pathogenic_max: null,
        },
        status: 'pathogenic',
        calls: [
          {
            sample: 'PROBAND',
            genotype: '0/1',
            status: 'pathogenic',
            note: null,
            alleles: [
              { repeat_count: 18, bp_length: 54, support_reads: null, interruption_label: null, status: 'normal' },
              { repeat_count: 40, bp_length: 120, support_reads: 12, interruption_label: null, status: 'pathogenic' },
            ],
          },
        ],
      },
    ],
  },
  paraphase: {
    catalogue: { regions: 14, digest: '0123456789abcdef' },
    genes: [
      {
        gene: 'SMN1',
        region_id: 'smn1_smn2',
        region: 'SMN1/SMN2',
        medically_relevant: true,
        status_rule: { field: 'smn1_cn', pathogenic: { eq: 0 }, carrier: { eq: 1 }, normal: { gte: 2 } },
        expected_copy_number: { smn1_cn: [1, 1] },
        samples: [
          {
            sample: 'PROBAND',
            status: 'carrier',
            copy_number_signal: true,
            total_cn: 3,
            gene_cn: 1,
            highest_total_cn: 3,
            sample_sex: 'male',
            copy_number: [{ key: 'smn1_cn', label: 'SMN1 CN', value: 1 }],
            fusion_count: null,
            loaded_at: '2026-10-02T00:00:00+00:00',
          },
        ],
      },
      { gene: 'GBA', region_id: 'gba', region: 'GBA/GBAP1', medically_relevant: true, status_rule: null, samples: [] },
    ],
  },
};
