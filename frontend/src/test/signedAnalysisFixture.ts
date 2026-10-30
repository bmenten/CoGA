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
    // Since CoGA froze the near-boundary warnings (repeat_count_units.py): the margin in force,
    // each locus's QA record of TRGT's counting units and each allele's warning.
    count_unit_margin: 3,
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
        count_units: {
          validated: true,
          reason: 'Validation report 7',
          updated_by: 'qa-lead',
          updated_at: '2026-10-15T09:00:00+00:00',
        },
        calls: [
          {
            sample: 'PROBAND',
            genotype: '0/1',
            status: 'pathogenic',
            note: null,
            alleles: [
              {
                repeat_count: 18,
                bp_length: 54,
                support_reads: null,
                interruption_label: null,
                status: 'normal',
                count_unit_warning: null,
              },
              {
                repeat_count: 40,
                bp_length: 120,
                support_reads: 12,
                interruption_label: null,
                status: 'pathogenic',
                count_unit_warning: null,
              },
            ],
          },
        ],
      },
      {
        // A grey-zone 53 at a locus QA has not validated: a premutation of 55 can read 53.
        locus_id: 'FXS_FMR1',
        gene: 'FMR1',
        disease: 'Fragile X syndrome',
        inheritance: 'XLD',
        chrom: 'chrX',
        start: 147912049,
        end: 147912111,
        motif: 'CGG',
        thresholds: {
          intermediate_min: 45,
          premutation_min: 55,
          pathogenic_min: 201,
          benign_min: 5,
          benign_max: 44,
          pathogenic_max: 2000,
        },
        status: 'intermediate',
        count_units: { validated: false, reason: null, updated_by: null, updated_at: null },
        calls: [
          {
            sample: 'MOTHER',
            genotype: '1/2',
            status: 'intermediate',
            note: null,
            alleles: [
              {
                repeat_count: 30,
                bp_length: 90,
                support_reads: 20,
                interruption_label: null,
                status: 'normal',
                count_unit_warning: null,
              },
              {
                repeat_count: 53,
                bp_length: 159,
                support_reads: 18,
                interruption_label: null,
                status: 'intermediate',
                count_unit_warning: {
                  repeat_count: 53,
                  margin: 3,
                  boundaries: [{ repeat_count: 55, status: 'premutation' }],
                },
              },
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
  // What each coverage track held, frozen since 2026-10-20 (signed_record_sections.coverage_tracks).
  coverage_tracks: {
    tracks: [
      {
        sample: 'MOTHER',
        source: 'web',
        content: 'unknown',
        files: [
          {
            file: 'MOTHER.bed',
            rows: 2,
            loaded_at: '2026-10-01T09:00:00+00:00',
            loaded_from: 'package',
            declared: 'unknown',
            normalization: null,
          },
        ],
      },
      {
        sample: 'PROBAND',
        source: 'web',
        content: 'read_depth',
        files: [
          {
            file: 'PROBAND.upload.bed',
            rows: 1200,
            loaded_at: '2026-10-02T09:00:00+00:00',
            loaded_from: 'upload',
            declared: 'read_depth',
            normalization: null,
          },
        ],
      },
      {
        sample: 'PROBAND',
        source: 'wisecondorx',
        content: 'log2_ratio',
        files: [
          {
            file: 'PROBAND_bins.bed',
            rows: 3000,
            loaded_at: '2026-10-01T09:00:00+00:00',
            loaded_from: 'package',
            declared: null,
            normalization: null,
          },
        ],
      },
      'not a track',
    ],
  },
};

// The same repeat expansions as a record signed before CoGA froze the near-boundary warnings and
// QA's records of TRGT's counting units holds them: no margin, no record per locus, no warning
// beside an allele.
export const SIGNED_REPEAT_EXPANSIONS_BEFORE_COUNT_UNITS = {
  catalogue: SIGNED_ANALYSIS.repeat_expansions.catalogue,
  loci: SIGNED_ANALYSIS.repeat_expansions.loci.map((locus) => ({
    ...Object.fromEntries(Object.entries(locus).filter(([key]) => key !== 'count_units')),
    calls: locus.calls.map((call) => ({
      ...call,
      alleles: call.alleles.map((allele) =>
        Object.fromEntries(Object.entries(allele).filter(([key]) => key !== 'count_unit_warning')),
      ),
    })),
  })),
};

// A PGT family's embryo calls at the ROI, as its record freezes them since 2026-10-20
// (backend/app/services/pgt_analysis.py, the snapshot key pgt_analysis).
export const SIGNED_PGT_ANALYSIS = {
  region_of_interest: { query: 'GENE1', label: 'GENE1', source: 'gene', chrom: '7', start: 30500000, end: 30600000 },
  window: { chrom: '7', start: 29500000, end: 31600000 },
  inheritance_model: { recorded: null, applied: 'AD', mode: 'dominant' },
  members: [
    { sample: 'E1', role: 'embryo', sex: 'female', affected: false, clinical_status: 'unknown', carrier_status: 'unknown', carrier_type: null },
    { sample: 'E2', role: 'embryo', sex: 'und', affected: false, clinical_status: 'unknown', carrier_status: 'unknown', carrier_type: null },
    { sample: 'E3', role: 'embryo', sex: 'female', affected: false, clinical_status: 'unknown', carrier_status: 'unknown', carrier_type: null },
    { sample: 'FATHER', role: 'father', sex: 'male', affected: true, clinical_status: 'affected', carrier_status: 'unknown', carrier_type: null },
    { sample: 'INDEX', role: 'proband', sex: 'female', affected: true, clinical_status: 'affected', carrier_status: 'unknown', carrier_type: null },
    { sample: 'MOTHER', role: 'mother', sex: 'female', affected: false, clinical_status: 'unaffected', carrier_status: 'not_carrier', carrier_type: null },
  ],
  disease_haplotype_resolved: true,
  disease_haplotypes: [{ origin: 'paternal', homolog: '1', kind: 'dominant' }],
  phase_corrections: [
    { parent: 'FATHER', side: 'father', chr: '7', position: 30400000, end: 30450000, children_switching: 3, children: 3 },
  ],
  embryos: [
    {
      sample: 'E1',
      sex: 'female',
      call: 'affected_or_at_risk',
      roi_not_covered: false,
      sex_dependent: null,
      inherited: [
        { origin: 'maternal', homolog: '0', start: 29000000, end: 32000000 },
        { origin: 'paternal', homolog: '1', start: 29000000, end: 32000000 },
      ],
      recombinations: [],
      qc: {
        informative_sites: 40,
        mendel_errors: 1,
        mendel_rate: 0.025,
        paternal_markers: 22,
        maternal_markers: 18,
        paternal_markers_left: 10,
        paternal_markers_right: 12,
        maternal_markers_left: 8,
        maternal_markers_right: 9,
        paternal_discordance: 0.01,
        maternal_discordance: 0.02,
      },
      qc_verdict: {
        status: 'warning',
        sides: ['paternal'],
        findings: [
          { measure: 'informative_sites', value: 40, threshold: 50 },
          { measure: 'mendel_rate', value: 0.025, threshold: 0.02 },
        ],
      },
      mixed_homologs: [],
    },
    {
      sample: 'E2',
      sex: 'und',
      call: 'uninformative',
      roi_not_covered: true,
      sex_dependent: null,
      inherited: [],
      recombinations: [30550000],
      qc: null,
      qc_verdict: { status: 'not_counted', sides: ['paternal'], findings: [] },
      mixed_homologs: [],
    },
    {
      sample: 'E3',
      sex: 'female',
      call: 'unaffected_non_carrier',
      roi_not_covered: false,
      sex_dependent: null,
      inherited: [
        { origin: 'maternal', homolog: '1', start: 29000000, end: 32000000 },
        { origin: 'paternal', homolog: '0', start: 29000000, end: 32000000 },
      ],
      recombinations: [],
      qc: {
        informative_sites: 120,
        mendel_errors: 0,
        mendel_rate: 0,
        paternal_markers: 40,
        maternal_markers: 35,
        paternal_markers_left: 20,
        paternal_markers_right: 20,
        maternal_markers_left: 17,
        maternal_markers_right: 18,
        paternal_discordance: 0,
        maternal_discordance: 0,
      },
      qc_verdict: { status: 'pass', sides: ['paternal'], findings: [] },
      mixed_homologs: [],
    },
    { call: 'not a call' },
  ],
  inputs: {
    haplotype_sources: [
      { sample: 'E1', source: 'glimpse2', filename: 'family.vcf.gz', blocks: 30, loaded_at: '2026-10-01T09:00:00+00:00' },
      { sample: 'FATHER', source: 'glimpse2', filename: 'family.vcf.gz', blocks: 12, loaded_at: '2026-10-01T09:00:00+00:00' },
    ],
    blocks_read: 6,
    blocks_digest: 'abcdef0123456789',
    markers_truncated: false,
  },
  rules: {
    window_flank: 1000000,
    recombination_flank: 250000,
    phase_switch_uncertainty: 2000000,
    // The phasing-QC thresholds each embryo's verdict was made against (pgt_embryo_qc.py).
    qc_thresholds: {
      min_informative_sites: 50,
      max_mendel_rate: 0.02,
      min_markers_per_flank: 6,
      max_marker_discordance: 0.1,
      min_discordance_markers: 20,
    },
  },
};

// The QC counts a record holds from before the markers on each flank of the ROI were counted apart
// (and before their discordance was measured).
const FLANK_COUNTS = [
  'paternal_markers_left',
  'paternal_markers_right',
  'maternal_markers_left',
  'maternal_markers_right',
  'paternal_discordance',
  'maternal_discordance',
];
const withoutFlankCounts = (qc: unknown) =>
  qc && typeof qc === 'object' ? Object.fromEntries(Object.entries(qc).filter(([key]) => !FLANK_COUNTS.includes(key))) : qc;

// The same analysis as a PGT family's record signed before CoGA checked each embryo's phasing QC
// against thresholds holds it: no verdict beside the embryos' QC, no thresholds, and no markers
// counted on each flank of the ROI.
export const SIGNED_PGT_ANALYSIS_BEFORE_QC_VERDICT = {
  ...SIGNED_PGT_ANALYSIS,
  embryos: SIGNED_PGT_ANALYSIS.embryos.map((embryo) =>
    Object.fromEntries(
      Object.entries(embryo)
        .filter(([key]) => key !== 'qc_verdict' && key !== 'mixed_homologs')
        .map(([key, value]) => [key, key === 'qc' ? withoutFlankCounts(value) : value]),
    ),
  ),
  rules: { window_flank: 1000000, recombination_flank: 250000, phase_switch_uncertainty: 2000000 },
};
