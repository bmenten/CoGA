// REQ-TRACE-007: a signed version is rendered from its frozen snapshot alone. These pin how
// the snapshot is read: every section the record holds, a section an older record predates
// as absent (never as empty), and an entry that cannot be read kept, not dropped.
import { describe, expect, it } from 'vitest';

import {
  ANALYTICAL_SIGN_OFF_STATEMENT,
  coverageTrackCaller,
  describeBreachedMetric,
  describeCatalogue,
  describeCnvKnowledgebase,
  describeCompoundHetPair,
  describeCoverageContent,
  describeCoverageFiles,
  describeCoverageTrack,
  describeCoverageTracks,
  describeCountUnitMargin,
  describeCountUnits,
  describeDiseaseHaplotypes,
  describeEmbryoCall,
  describeEmbryoQc,
  describeFrozenEvidenceValues,
  describeGenePanel,
  describeGeneReferenceReleases,
  describeGenotype,
  describeInherited,
  describeModuleVersions,
  describeNiptCoverage,
  describeNiptFetalFraction,
  describeNiptFetalSex,
  describeNiptFunnel,
  describeNiptVariant,
  describeNoCall,
  describeNotCompared,
  describePgtInheritance,
  describePgtInputs,
  describePgtMembers,
  describeQcThresholds,
  describeRepeatCall,
  describeRepeatLocusCalls,
  describeRepeatThresholds,
  describeReportSections,
  describeSignedDrift,
  describeSignedCountUnitWarning,
  describeSignedQcVerdict,
  describeSignedStructuralDrift,
  describeSmallVariantDescription,
  describeSmallVariantEvidenceChange,
  describeSmallVariantLocus,
  describeStructuralEvidenceChange,
  describeStructuralVariantDescription,
  homologLabel,
  isNiptRecord,
  liveReportSignedNotice,
  liveReportSignedState,
  NIPT_NOT_CLASSIFIED_REASONS,
  notComparedSentence,
  paraphaseStatusWords,
  parseSignedReport,
  parseSignedVersionParam,
  reportViewSearch,
  type SignedCoverageTrack,
  type SignedPgtAnalysis,
  type SignedRepeatExpansions,
  type SignedReport,
} from '../signedReportRecord';
import { describeCountUnitWarning } from '../../../lib/repeatStatus';
import {
  SIGNED_ANALYSIS,
  SIGNED_PGT_ANALYSIS,
  SIGNED_PGT_ANALYSIS_BEFORE_QC_VERDICT,
  SIGNED_REPEAT_EXPANSIONS_BEFORE_COUNT_UNITS,
} from '../../../test/signedAnalysisFixture';

// The evidence a small-variant classification froze since CLIN-7: the annotation identity,
// ClinVar, and the primary transcript's frequencies and in-silico scores
// (backend small_variant_review_acmg.build_evidence_snapshot).
const FROZEN_EVIDENCE = {
  annotation_version: 'v1',
  annotation_set_hash: 'h1',
  clinvar: null,
  transcript_id: 'ENST00000000001',
  frequencies: {
    gnomad_af: 3.1e-5,
    gnomad_exomes_af: 1.2e-5,
    gnomad_genomes_af: 3.1e-5,
    gnomad_popmax_af: 0.0002,
    topmed_af: null,
    gnomad_ac: 4,
    gnomad_hom_count: 0,
    gnomad_hemi_count: null,
  },
  in_silico: {
    cadd_phred: 24.3,
    revel: 0.412,
    spliceai_max: 0.02,
    sift: 'deleterious(0.01)',
    polyphen: 'probably_damaging(0.981)',
    alpha_missense_class: null,
    alpha_missense_pathogenicity: 'high',
  },
  captured_at: '2026-06-20T08:30:00+00:00',
};

const SNAPSHOT = {
  family_id: 'F1',
  assembly: 'GRCh38',
  modules: [
    { key: 'assembly', label: 'Reference assembly', version: 'GRCh38', detail: '2013-12-01', layer: 'reference' },
    { key: 'gencode', label: 'GENCODE', version: '49', detail: null, by_modality: { snv: '49', sv: '45' } },
    { key: 'spliceai', label: 'SpliceAI', version: null, detail: null },
  ],
  software: { version: '0.1.0', git_sha: 'abc1234def567' },
  drift: {
    checked: 2,
    drifted_count: 1,
    drifted: [{ variant_id: '1-100-A-G', status: 'no_snapshot', acmg_class: 'acmg_class_3' }],
  },
  structural_drift: {
    checked: 3,
    drifted_count: 2,
    drifted: [
      {
        variant_id: 'sv-1',
        status: 'drifted',
        classified_by: 'bob',
        changed: ['gene_symbols', 7],
        evidence_from: { chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4', 'TXNL1'], pli: 0.99 },
        evidence_to: { chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4'], pli: 'high' },
      },
      { variant_id: 'sv-2', status: 'no_snapshot', cnv_class: 'cnv_class_4' },
    ],
  },
  acknowledged_drift: true,
  drift_acknowledgement_reason: 'Reviewed at the case meeting.',
  sample_qc: {
    overall_status: 'warn',
    application_label: 'Trio WGS',
    sex_checks: [{ sample_id: 'P1', status: 'pass', message: 'Sex matches.' }],
    relatedness_checks: [
      { sample_a: 'F1', sample_b: 'P1', expected_relationship: 'parent-child', status: 'pass', message: 'Parent–child confirmed.' },
    ],
    mendelian_checks: [{ child: 'P1', parents: ['F1'], status: 'warn', message: 'Elevated Mendelian-error rate.' }],
    paternity_check: null,
    fetal_sex_check: null,
    category_qc_check: null,
    notes: ['Genotypes from the trio callset.'],
  },
  acknowledged_qc: true,
  qc_acknowledgement_reason: 'Rate explained by low coverage.',
  sequencing_qc: {
    profile_key: 'wgs',
    profile_label: 'WGS default',
    thresholds: {},
    samples: {
      P1: {
        verdict: 'warn',
        metrics: [
          { metric_key: 'mean_coverage', label: 'Mean coverage', unit: '×', value: 18, warn_value: 20, error_value: 10, verdict: 'warn', direction: 'lower_is_worse' },
          { metric_key: 'q30', label: 'Q30', unit: '%', value: 93, warn_value: 85, error_value: 80, verdict: 'pass' },
        ],
      },
    },
  },
  import_incomplete: null,
  import_unfinished: {},
  reported_variants: [
    {
      variant_id: '17-43000000-A-G',
      acmg_class: 'acmg_class_4',
      acmg: {
        criteria: [
          { code: 'PS3', strength: 'strong', accepted: true, evidence: 'Functional assay.' },
          { code: 'PM2', strength: 'moderate', accepted: true, evidence: null },
          { code: 'BP4', strength: 'supporting', accepted: false, evidence: null },
          'not a criterion',
        ],
        point_total: 6,
        classification: 'Likely Pathogenic - class 4',
      },
      tags: ['acmg_class_4', 'report'],
      note: 'Signed interpretation.',
      evidence_snapshot: { annotation_version: 'v1', clinvar: 'Likely_pathogenic', captured_at: '2026-06-20T08:30:00+00:00' },
    },
    // Classified through the review tags only, never through ACMG classify.
    { variant_id: '2-200-C-T', acmg_class: null, acmg: null, tags: ['acmg_class_3', 'report'], note: null, evidence_snapshot: null },
    'an entry that is not an object',
  ],
  reported_structural_variants: [
    {
      variant_id: '2-1000-50000-DEL---',
      variant_key: 123,
      classification: null,
      cnv_class: 'cnv_class_5',
      cnv_point_total: 1,
      cnv_acmg: {
        kind: 'loss',
        criteria: [
          { code: '2A', points: 1, accepted: true, evidence: 'Covers the HI region.' },
          { code: '3A', points: 0, accepted: false },
        ],
        point_total: 1,
        classification: 'Pathogenic - class 5',
      },
      tags: ['report'],
      note: 'CNV note.',
    },
  ],
};

describe('parseSignedReport', () => {
  it('reads each section the signed record holds', () => {
    const record = parseSignedReport(SNAPSHOT)!;

    expect(record.familyId).toBe('F1');
    expect(record.assembly).toBe('GRCh38');
    expect(record.software).toEqual({ version: '0.1.0', gitSha: 'abc1234def567' });
    expect(record.drift).toEqual({
      checked: 2,
      driftedCount: 1,
      drifted: [
        {
          variantId: '1-100-A-G',
          status: 'no_snapshot',
          clinvarFrom: null,
          clinvarTo: null,
          changed: [],
          evidenceFrom: null,
          evidenceTo: null,
          classifiedBy: null,
        },
      ],
    });
    // The SV/CNV classifications' drift: a field of the wrong type is dropped, not guessed.
    expect(record.structuralDrift).toEqual({
      checked: 3,
      driftedCount: 2,
      drifted: [
        {
          variantId: 'sv-1',
          status: 'drifted',
          changed: ['gene_symbols'],
          evidenceFrom: { source: null, sv_type: null, chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4', 'TXNL1'], pli: 0.99, inheritance: null },
          evidenceTo: { source: null, sv_type: null, chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4'], pli: null, inheritance: null },
          classifiedBy: 'bob',
        },
        { variantId: 'sv-2', status: 'no_snapshot', changed: [], evidenceFrom: null, evidenceTo: null, classifiedBy: null },
      ],
    });
    expect(record.driftAcknowledgement).toEqual({ acknowledged: true, reason: 'Reviewed at the case meeting.' });
    expect(record.sampleQc?.overallStatus).toBe('warn');
    expect(record.sampleQc?.checks.map((check) => check.label)).toEqual([
      'Sex of P1',
      'Relatedness of F1 and P1 (expected parent-child)',
      'Mendelian consistency of P1',
    ]);
    expect(record.sampleQc?.notes).toEqual(['Genotypes from the trio callset.']);
    expect(record.qcAcknowledgement).toEqual({ acknowledged: true, reason: 'Rate explained by low coverage.' });
    expect(record.sequencingQc?.profileLabel).toBe('WGS default');
    // Only the metrics outside their cut-offs.
    expect(record.sequencingQc?.samples).toEqual([
      {
        sampleId: 'P1',
        verdict: 'warn',
        breached: [
          { label: 'Mean coverage', value: 18, unit: '×', verdict: 'warn', warnValue: 20, errorValue: 10, direction: 'lower_is_worse' },
        ],
      },
    ]);
    expect(record.importIncomplete).toBeNull();
    expect(record.importUnfinished).toEqual([]);
  });

  it('reads a reported small variant as the live report reads its review, from the record alone', () => {
    const [classified, tagged, unreadable] = parseSignedReport(SNAPSHOT)!.reportedVariants!;

    expect(classified.variantId).toBe('17-43000000-A-G');
    expect(classified.classification).toBe('Likely Pathogenic - class 4');
    expect(classified.pointTotal).toBe(6);
    // The accepted criteria, the ones the classification rests on, named from the catalogue.
    expect(classified.criteria.map((criterion) => [criterion.code, criterion.strengthLabel])).toEqual([
      ['PS3', 'Strong'],
      ['PM2', 'Moderate'],
    ]);
    expect(classified.criteria[0].evidence).toBe('Functional assay.');
    expect(classified.criteria[0].name).not.toBe('PS3');
    // An entry that cannot be read is counted, not dropped.
    expect(classified.unreadableCriteria).toBe(1);
    // Frozen before CoGA froze the consequence, frequencies, in-silico scores and gene
    // missense Z: the record holds none.
    expect(classified.evidence).toEqual({
      clinvar: 'Likely_pathogenic',
      annotationVersion: 'v1',
      capturedAt: '2026-06-20T08:30:00+00:00',
      transcriptId: null,
      consequence: null,
      frequencies: null,
      inSilico: null,
      geneConstraint: null,
      geneConstraintSource: null,
    });

    // No ACMG criteria: the class its classification tag names; no evidence was frozen.
    expect(tagged.classification).toBe('VUS - class 3');
    expect(tagged.pointTotal).toBeNull();
    expect(tagged.criteria).toEqual([]);
    expect(tagged.evidence).toBeNull();

    expect(unreadable.variantId).toBeNull();
    // Reported by their own tag: no compound-het pair review is frozen with them.
    expect([classified.compoundHet, tagged.compoundHet, unreadable.compoundHet]).toEqual([null, null, null]);
  });

  it('reads the compound-het pair review that reports a variant (TRACE-19)', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      reported_variants: [
        {
          variant_id: '1-3001-A-G',
          acmg_class: null,
          acmg: null,
          tags: [],
          note: null,
          evidence_snapshot: null,
          compound_het: {
            partner_variant_ids: ['1-10000-A-G'],
            gene: 'GENE_RECESS',
            classification: 'likely_pathogenic_pair',
            tags: ['report'],
            note: 'In trans in the index.',
            phase_status: 'likely_in_trans',
          },
        },
        {
          variant_id: '1-10000-A-G',
          acmg_class: null,
          acmg: null,
          tags: [],
          note: null,
          evidence_snapshot: null,
          compound_het: { partner_variant_ids: ['1-3001-A-G'], gene: null, classification: null, tags: ['report'], note: null, phase_status: null },
        },
      ],
    })!;
    const [maternal, paternal] = record.reportedVariants!;

    expect(maternal.compoundHet).toEqual({
      partnerVariantIds: ['1-10000-A-G'],
      gene: 'GENE_RECESS',
      classification: 'likely_pathogenic_pair',
      tags: ['report'],
      note: 'In trans in the index.',
      phaseStatus: 'likely_in_trans',
    });
    expect(describeCompoundHetPair(maternal.compoundHet!)).toBe(
      'Reported through its compound-het pair review: paired with 1-10000-A-G in GENE_RECESS (likely in trans); the pair is tagged report and classified likely_pathogenic_pair.',
    );
    expect(describeCompoundHetPair(paternal.compoundHet!)).toBe(
      'Reported through its compound-het pair review: paired with 1-3001-A-G (phase unknown); the pair is tagged report.',
    );
  });

  it('reads the frequencies and in-silico scores frozen with a classification (CLIN-7)', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      reported_variants: [
        {
          variant_id: '17-43000000-A-G',
          acmg_class: 'acmg_class_4',
          acmg: null,
          tags: ['report'],
          note: null,
          evidence_snapshot: FROZEN_EVIDENCE,
        },
      ],
    })!;
    const [variant] = record.reportedVariants!;

    expect(variant.evidence?.transcriptId).toBe('ENST00000000001');
    // Every value the record holds, in the order a reviewer reads them, written as the report
    // writes it; one without a value, or of the wrong type, reads as none.
    expect(variant.evidence?.frequencies).toEqual([
      { label: 'gnomAD AF', value: '3.10e-5' },
      { label: 'gnomAD exomes AF', value: '1.20e-5' },
      { label: 'gnomAD genomes AF', value: '3.10e-5' },
      { label: 'gnomAD popmax AF', value: '0.0002' },
      { label: 'TOPMed AF', value: null },
      { label: 'gnomAD allele count', value: '4' },
      { label: 'gnomAD homozygotes', value: '0' },
      { label: 'gnomAD hemizygotes', value: null },
    ]);
    expect(variant.evidence?.inSilico).toEqual([
      { label: 'CADD', value: '24.3' },
      { label: 'REVEL', value: '0.412' },
      { label: 'SpliceAI', value: '0.02' },
      { label: 'SIFT', value: 'deleterious(0.01)' },
      { label: 'PolyPhen', value: 'probably_damaging(0.981)' },
      { label: 'AlphaMissense class', value: null },
      { label: 'AlphaMissense score', value: null },
    ]);
    expect(describeFrozenEvidenceValues(variant.evidence!)).toBe(
      'Read from its primary transcript ENST00000000001: population frequencies gnomAD AF 3.10e-5, ' +
        'gnomAD exomes AF 1.20e-5, gnomAD genomes AF 3.10e-5, gnomAD popmax AF 0.0002, TOPMed AF not annotated, ' +
        'gnomAD allele count 4, gnomAD homozygotes 0 and gnomAD hemizygotes not annotated; in silico scores ' +
        'CADD 24.3, REVEL 0.412, SpliceAI 0.02, SIFT deleterious(0.01), PolyPhen probably_damaging(0.981), ' +
        'AlphaMissense class not annotated and AlphaMissense score not annotated.',
    );
    // A record frozen before them holds none, and says nothing of them.
    const older = parseSignedReport(SNAPSHOT)!.reportedVariants![0].evidence!;
    expect(describeFrozenEvidenceValues(older)).toBeNull();
    // Nor does one frozen before the consequence and the gene's missense Z were: no group
    // of them is read, so none is said to be "not annotated".
    expect(variant.evidence?.consequence).toBeNull();
    expect(variant.evidence?.geneConstraint).toBeNull();
  });

  it('reads the consequence, the LoF call and the gene missense Z frozen with a classification', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      reported_variants: [
        {
          variant_id: '17-43000000-A-G',
          acmg_class: 'acmg_class_4',
          acmg: null,
          tags: ['report'],
          note: null,
          evidence_snapshot: {
            ...FROZEN_EVIDENCE,
            consequence: { effect: 'missense_variant', impact: 'MODERATE', lof: null },
            gene_constraint: { gene_missense_z: 3.521 },
          },
        },
        {
          // A stop gained: no missense Z is frozen for it, as PP2 does not read one.
          variant_id: '17-43000100-C-T',
          acmg_class: 'acmg_class_5',
          acmg: null,
          tags: ['report'],
          note: null,
          evidence_snapshot: {
            ...FROZEN_EVIDENCE,
            consequence: { effect: 'stop_gained', impact: 'HIGH', lof: 'HC' },
          },
        },
      ],
    })!;
    const [variant, stopGained] = record.reportedVariants!;

    expect(variant.evidence?.consequence).toEqual([
      { label: 'consequence', value: 'missense_variant' },
      { label: 'impact', value: 'MODERATE' },
      { label: 'LoF', value: null },
    ]);
    expect(variant.evidence?.geneConstraint).toEqual([{ label: 'gnomAD missense Z', value: '3.52' }]);
    // The consequence first, read from the primary transcript; the missense Z is the gene's.
    expect(describeFrozenEvidenceValues(variant.evidence!)).toBe(
      'Read from its primary transcript ENST00000000001: consequence missense_variant, impact MODERATE and LoF ' +
        'not annotated; population frequencies gnomAD AF 3.10e-5, gnomAD exomes AF 1.20e-5, gnomAD genomes AF ' +
        '3.10e-5, gnomAD popmax AF 0.0002, TOPMed AF not annotated, gnomAD allele count 4, gnomAD homozygotes 0 ' +
        'and gnomAD hemizygotes not annotated; in silico scores CADD 24.3, REVEL 0.412, SpliceAI 0.02, SIFT ' +
        'deleterious(0.01), PolyPhen probably_damaging(0.981), AlphaMissense class not annotated and ' +
        "AlphaMissense score not annotated. From its gene's profile: gnomAD missense Z 3.52.",
    );
    // The stop gained's record holds no missense Z, and says nothing of one.
    expect(stopGained.evidence?.geneConstraint).toBeNull();
    expect(describeFrozenEvidenceValues(stopGained.evidence!)).toMatch(
      /^Read from its primary transcript ENST00000000001: consequence stop_gained, impact HIGH and LoF HC; .*score not annotated\.$/,
    );
    // A missense Z the gene's profile did not hold, or one of the wrong type, reads as none.
    const without = parseSignedReport({
      ...SNAPSHOT,
      reported_variants: [
        {
          variant_id: '17-43000000-A-G',
          tags: ['report'],
          evidence_snapshot: {
            transcript_id: null,
            consequence: { effect: 7, impact: 'MODERATE' },
            gene_constraint: { gene_missense_z: 'high' },
          },
        },
      ],
    })!.reportedVariants![0].evidence!;
    expect(describeFrozenEvidenceValues(without)).toBe(
      "Read from its primary transcript: consequence not annotated and impact MODERATE. From its gene's " +
        'profile: gnomAD missense Z not annotated.',
    );
  });

  it('says where the frozen missense Z PP2 read came from (TRACE-15)', () => {
    // PP2 reads the gene profile's missense Z, else the VCF's own MISSENSE_Z; the snapshot freezes
    // the value with its source. One frozen before the source was holds the gene profile's.
    const missenseZ = (gene_constraint: Record<string, unknown>) =>
      parseSignedReport({
        ...SNAPSHOT,
        reported_variants: [
          { variant_id: '17-43000000-A-G', tags: ['report'], evidence_snapshot: { gene_constraint } },
        ],
      })!.reportedVariants![0].evidence!;

    const fromVcf = missenseZ({ gene_missense_z: 4.2, gene_missense_z_source: 'vcf' });
    expect(fromVcf.geneConstraint).toEqual([{ label: 'gnomAD missense Z', value: '4.20' }]);
    expect(fromVcf.geneConstraintSource).toBe('vcf');
    expect(describeFrozenEvidenceValues(fromVcf)).toBe(
      "From the VCF's MISSENSE_Z, as its gene's profile held none: gnomAD missense Z 4.20.",
    );
    const fromProfile = missenseZ({ gene_missense_z: 3.521, gene_missense_z_source: 'gene_profile' });
    expect(describeFrozenEvidenceValues(fromProfile)).toBe("From its gene's profile: gnomAD missense Z 3.52.");
    const earlier = missenseZ({ gene_missense_z: 3.521 });
    expect(earlier.geneConstraintSource).toBe('gene_profile');
    expect(describeFrozenEvidenceValues(earlier)).toBe("From its gene's profile: gnomAD missense Z 3.52.");
    // Neither held one: the record says so, rather than "not annotated" in one of them.
    const neither = missenseZ({ gene_missense_z: null, gene_missense_z_source: null });
    expect(neither.geneConstraintSource).toBeNull();
    expect(describeFrozenEvidenceValues(neither)).toBe("Neither its gene's profile nor the VCF held a gnomAD missense Z.");
    // A value whose source the record does not name is not given one.
    expect(describeFrozenEvidenceValues(missenseZ({ gene_missense_z: 3.6, gene_missense_z_source: 7 }))).toBe(
      'gnomAD missense Z 3.60, from a source the record does not name.',
    );
  });

  it('reads what moved in a classification’s frozen values when the version was signed', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      drift: {
        checked: 1,
        drifted_count: 1,
        drifted: [
          {
            variant_id: '17-43000000-A-G',
            status: 'drifted',
            clinvar_from: null,
            clinvar_to: null,
            changed: ['revel', 'annotations', 3],
            evidence_from: { revel: 0.412 },
            evidence_to: { revel: 0.951 },
            classified_by: 'alice',
          },
        ],
      },
    })!;
    const [item] = record.drift!.drifted;
    expect(item.changed).toEqual(['revel', 'annotations']);
    expect(describeSignedDrift(item)).toBe('REVEL 0.412 → 0.951');
  });

  it('reads a reported structural variant with its ClinGen CNV scoring', () => {
    const [sv] = parseSignedReport(SNAPSHOT)!.reportedStructuralVariants!;

    expect(sv.variantId).toBe('2-1000-50000-DEL---');
    expect(sv.variantKey).toBe('123');
    // No review label: the CNV scoring's.
    expect(sv.classification).toBe('Pathogenic - class 5');
    expect(sv.pointTotal).toBe(1);
    expect(sv.criteria).toEqual([
      {
        code: '2A',
        name: 'Complete overlap of an established haploinsufficient (HI) gene/region',
        points: 1,
        evidence: 'Covers the HI region.',
      },
    ]);
    expect(sv.note).toBe('CNV note.');
  });

  it('reads a section an older record predates as absent, never as empty', () => {
    const older = {
      family_id: 'F1',
      assembly: 'GRCh38',
      modules: [],
      reported_variants: [],
    };
    const record = parseSignedReport(older)!;

    expect(record.software).toBeNull();
    expect(record.drift).toBeNull();
    // Signed before CoGA froze the SV/CNV evidence: the record holds no drift for them.
    expect(record.structuralDrift).toBeNull();
    expect(record.sampleQc).toBeNull();
    expect(record.sequencingQc).toBeNull();
    expect(record.importIncomplete).toBe('absent');
    expect(record.importUnfinished).toBe('absent');
    // Signed before CoGA froze the reported SVs: the record holds none.
    expect(record.reportedStructuralVariants).toBeNull();
    expect(record.driftAcknowledgement).toEqual({ acknowledged: null, reason: null });
    expect(record.modules).toEqual([]);
  });

  it('reads an incomplete import and QC cut-offs that could not be resolved', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      import_incomplete: { at: '2026-06-01T09:00:00+00:00', failed_datasets: ['sv'], imported_datasets: ['snv'], job_id: 'job-1' },
      acknowledged_import_incomplete: true,
      import_incomplete_acknowledgement_reason: 'SVs not requested.',
      sequencing_qc: { thresholds: {}, samples: {}, unavailable: 'QC thresholds could not be resolved' },
    })!;

    expect(record.importIncomplete).toEqual({
      at: '2026-06-01T09:00:00+00:00',
      failedDatasets: ['sv'],
      importedDatasets: ['snv'],
      jobId: 'job-1',
      failedJobs: { sv: 'job-1' },
      withoutJob: [],
    });
    expect(record.importAcknowledgement).toEqual({ acknowledged: true, reason: 'SVs not requested.' });
    expect(record.sequencingQc?.unavailable).toBe('QC thresholds could not be resolved');
  });

  it('reads an import that had not finished when the version was signed', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      import_unfinished: {
        'job-2': {
          job_id: 'job-2',
          at: '2026-10-02T09:12:00+00:00',
          datasets: ['haplotypes', 'snv'],
          finished_datasets: ['snv'],
        },
      },
      acknowledged_import_incomplete: true,
      import_incomplete_acknowledgement_reason: 'Haplotypes not used for this referral.',
    })!;

    expect(record.importUnfinished).toEqual([
      {
        key: 'job-2',
        jobId: 'job-2',
        at: '2026-10-02T09:12:00+00:00',
        datasets: ['haplotypes', 'snv'],
        finishedDatasets: ['snv'],
        wholeFamily: false,
      },
    ]);
    expect(record.importIncomplete).toBeNull();
    expect(record.importAcknowledgement).toEqual({
      acknowledged: true,
      reason: 'Haplotypes not used for this referral.',
    });
  });

  it('has no record to read without a snapshot object', () => {
    expect(parseSignedReport(null)).toBeNull();
    expect(parseSignedReport('{"family_id":"F1"}')).toBeNull();
    expect(parseSignedReport([])).toBeNull();
  });
});

describe('the signed report’s wording', () => {
  it('lists the module versions as the footer does, each modality where they differ', () => {
    expect(describeModuleVersions(parseSignedReport(SNAPSHOT)!.modules!)).toBe(
      'Reference assembly GRCh38 (2013-12-01) · GENCODE 49 (snv), 45 (sv)',
    );
    expect(describeModuleVersions([{ key: 'spliceai', version: null }])).toBeNull();
  });

  it('says why a classification counted as drift when the version was signed', () => {
    const item = {
      variantId: 'v',
      clinvarFrom: null,
      clinvarTo: null,
      changed: [],
      evidenceFrom: null,
      evidenceTo: null,
      classifiedBy: null,
    };
    expect(describeSignedDrift({ ...item, status: 'no_snapshot' })).toBe(
      'no frozen evidence (not saved through ACMG classify)',
    );
    expect(describeSignedDrift({ ...item, status: 'variant_missing' })).toBe('no longer present in the data');
    expect(describeSignedDrift({ ...item, status: 'drifted', clinvarFrom: 'VUS', clinvarTo: 'Pathogenic' })).toBe(
      'ClinVar VUS → Pathogenic',
    );
    expect(describeSignedDrift({ ...item, status: 'drifted' })).toBe('annotation set changed');
    expect(describeSignedDrift({ ...item, status: 'drifted', changed: ['annotations'] })).toBe(
      'annotation set changed',
    );
    // What moved in the frozen frequencies and in-silico scores, after the ClinVar significance.
    expect(
      describeSignedDrift({
        ...item,
        status: 'drifted',
        clinvarFrom: 'VUS',
        clinvarTo: 'Pathogenic',
        changed: ['clinvar', 'gnomad_af', 'revel', 'annotations'],
        evidenceFrom: { gnomad_af: 3.1e-5, revel: 0.412 },
        evidenceTo: { gnomad_af: 0.012, revel: 0.951 },
      }),
    ).toBe('ClinVar VUS → Pathogenic; gnomAD AF 3.10e-5 → 0.012; REVEL 0.412 → 0.951');
  });

  it('names what moved in a small-variant classification’s frozen values', () => {
    expect(
      describeSmallVariantEvidenceChange(
        ['transcript_id', 'revel', 'sift', 'gnomad_hom_count'],
        { transcript_id: 'ENST00000000001', revel: null, sift: 'tolerated(0.2)', gnomad_hom_count: 0 },
        { transcript_id: 'ENST00000000002', revel: 0.7, sift: 'deleterious(0.01)', gnomad_hom_count: 2 },
      ),
    ).toBe(
      'primary transcript ENST00000000001 → ENST00000000002; gnomAD homozygotes 0 → 2; REVEL n/a → 0.700; ' +
        'SIFT tolerated(0.2) → deleterious(0.01)',
    );
    // Two values that differ past the digits the report writes are written in full.
    expect(describeSmallVariantEvidenceChange(['revel'], { revel: 0.4121 }, { revel: 0.4124 })).toBe(
      'REVEL 0.4121 → 0.4124',
    );
    // Only the annotation set moved: nothing to name.
    expect(describeSmallVariantEvidenceChange(['annotations'], {}, {})).toBeNull();
    expect(describeSmallVariantEvidenceChange(['revel'], null, null)).toBe('REVEL n/a → n/a');
  });

  it('names a moved consequence, LoF call or gene missense Z, in the order a reviewer reads them', () => {
    // A missense variant called a stop gained now: the missense Z is read for no other
    // variant, so it is gone, not moved.
    expect(
      describeSmallVariantEvidenceChange(
        ['effect', 'impact', 'lof', 'revel', 'gene_missense_z', 'annotations'],
        { effect: 'missense_variant', impact: 'MODERATE', lof: null, revel: 0.412, gene_missense_z: 2.95 },
        { effect: 'stop_gained', impact: 'HIGH', lof: 'HC', revel: 0.951 },
      ),
    ).toBe(
      'consequence missense_variant → stop_gained; impact MODERATE → HIGH; LoF n/a → HC; REVEL 0.412 → 0.951; ' +
        'gnomAD missense Z 2.95 → n/a',
    );
    // Two values that differ past the two digits a missense Z is written with are written in full.
    expect(describeSmallVariantEvidenceChange(['gene_missense_z'], { gene_missense_z: 3.091 }, { gene_missense_z: 3.089 })).toBe(
      'gnomAD missense Z 3.091 → 3.089',
    );
  });

  it('names the source of a missense Z read from elsewhere (TRACE-15)', () => {
    const profile = { gene_missense_z: 3.52, gene_missense_z_source: 'gene_profile' };
    // The same value, read from the VCF now that the gene profile holds none.
    expect(
      describeSmallVariantEvidenceChange(['gene_missense_z_source'], profile, {
        gene_missense_z: 3.52,
        gene_missense_z_source: 'vcf',
      }),
    ).toBe('gnomAD missense Z 3.52 (gene profile) → 3.52 (VCF)');
    // The value and its source.
    expect(
      describeSmallVariantEvidenceChange(
        ['gene_missense_z', 'gene_missense_z_source'],
        { gene_missense_z: 4.2, gene_missense_z_source: 'vcf' },
        profile,
      ),
    ).toBe('gnomAD missense Z 4.20 (VCF) → 3.52 (gene profile)');
    // Gone with the missense consequence: neither is read now.
    expect(
      describeSmallVariantEvidenceChange(['effect', 'gene_missense_z', 'gene_missense_z_source'], profile, {
        effect: 'stop_gained',
      }),
    ).toBe('consequence n/a → stop_gained; gnomAD missense Z 3.52 (gene profile) → n/a');
    // Only the value moved: as before, without the source.
    expect(
      describeSmallVariantEvidenceChange(['gene_missense_z'], profile, { ...profile, gene_missense_z: 2.95 }),
    ).toBe('gnomAD missense Z 3.52 → 2.95');
  });

  it('reads a moved consequence, LoF call and gene missense Z in the drift a version was signed with', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      drift: {
        checked: 2,
        drifted_count: 2,
        drifted: [
          {
            // A missense variant whose gene's profile was refreshed.
            variant_id: '12-40100301-C-T',
            status: 'drifted',
            clinvar_from: 'uncertain_significance',
            clinvar_to: 'uncertain_significance',
            changed: ['gene_missense_z'],
            evidence_from: { effect: 'missense_variant', gene_missense_z: 2.95 },
            evidence_to: { effect: 'missense_variant', gene_missense_z: 3.52 },
            classified_by: 'alice',
          },
          {
            // A stop gained, re-annotated: it froze no missense Z.
            variant_id: '12-40100501-G-T',
            status: 'drifted',
            clinvar_from: 'uncertain_significance',
            clinvar_to: 'uncertain_significance',
            changed: ['effect', 'lof', 'annotations'],
            evidence_from: { effect: 'stop_gained', lof: 'LC' },
            evidence_to: { effect: 'stop_gained&splice_region_variant', lof: 'HC' },
            classified_by: 'alice',
          },
        ],
      },
    })!;
    const [missense, stopGained] = record.drift!.drifted;
    expect(describeSignedDrift(missense)).toBe('gnomAD missense Z 2.95 → 3.52');
    expect(describeSignedDrift(stopGained)).toBe('consequence stop_gained → stop_gained&splice_region_variant; LoF LC → HC');
  });

  it('says why an SV/CNV classification counted as drift when the version was signed', () => {
    const evidence = { chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4', 'TXNL1'], pli: 0.99, sv_type: 'DEL', source: 'needlr', inheritance: 'de_novo' };
    const item = { variantId: 'sv', changed: [], evidenceFrom: evidence, evidenceTo: evidence, classifiedBy: null };
    // A missing SV names the caller whose call is gone: another caller may still call it under
    // the same id (TRACE-12; it read as gone whoever called it).
    expect(describeSignedStructuralDrift({ ...item, status: 'variant_missing' })).toBe(
      'its needlr call is no longer present in the data',
    );
    expect(describeSignedStructuralDrift({ ...item, status: 'variant_missing', evidenceFrom: null })).toBe(
      'no longer present in the data',
    );
    expect(describeSignedStructuralDrift({ ...item, status: 'no_snapshot' })).toBe(
      'no frozen evidence (no CNV scoring saved)',
    );
    expect(describeSignedStructuralDrift({ ...item, status: 'unknown' })).toBe('its evidence could not be compared');
    expect(
      describeSignedStructuralDrift({
        ...item,
        status: 'drifted',
        changed: ['gene_symbols', 'pli'],
        evidenceTo: { ...evidence, gene_symbols: ['TCF4'], pli: 0.41 },
      }),
    ).toBe('genes TCF4, TXNL1 → TCF4; pLI 0.990 → 0.410');
    expect(describeSignedStructuralDrift({ ...item, status: 'drifted', changed: ['annotations'] })).toBe('annotation changed');
    expect(describeSignedStructuralDrift({ ...item, status: 'drifted' })).toBe('evidence changed');
  });

  it('names what moved in an SV/CNV classification’s evidence, in the words of its inputs', () => {
    // Coordinates under 1,000, so the locus reads the same in every locale.
    const from = { chrom: '18', start: 100, end: 400, sv_type: 'DEL', source: 'needlr', inheritance: 'de_novo', gene_symbols: [] };
    const to = { ...from, start: 200, sv_type: 'DUP', source: 'hificnv', inheritance: null };
    expect(describeStructuralEvidenceChange(['inheritance', 'sv_type', 'locus', 'source', 'gene_symbols'], from, to)).toBe(
      'genes none → none; inheritance de_novo → n/a; type DEL → DUP; locus chr18:100-400 → chr18:200-400; caller needlr → hificnv',
    );
    expect(describeStructuralEvidenceChange(['annotations'], from, to)).toBeNull();
    expect(describeStructuralEvidenceChange(['pli'], null, null)).toBe('pLI n/a → n/a');
  });

  it('names a metric outside its cut-offs with its value and limits', () => {
    expect(
      describeBreachedMetric({
        label: 'Mean coverage',
        value: 18,
        unit: '×',
        verdict: 'warn',
        warnValue: 20,
        errorValue: 10,
        direction: 'lower_is_worse',
      }),
    ).toBe('Mean coverage 18 × (Warning; warning 20 ×, fail 10 ×)');
  });

  // ENG-10: the signed version wrote the value as measured, "18.57 x", where the members
  // table's QC chip writes "18.6x".
  it('writes the value and its limits as the QC chip writes them', () => {
    const metric = { label: 'Mean depth', unit: 'x', verdict: 'warn', warnValue: 20, errorValue: 10, direction: 'lower_is_worse' };
    expect(describeBreachedMetric({ ...metric, value: 18.57 })).toBe('Mean depth 18.6x (Warning; warning 20x, fail 10x)');
    // Rounded onto its limit it would not read as the breach it is: 19.96 is not "20.0x".
    expect(describeBreachedMetric({ ...metric, value: 19.96 })).toBe('Mean depth 19.96x (Warning; warning 20x, fail 10x)');
    // A record from before the direction was kept reads it from the side of the limit.
    expect(describeBreachedMetric({ ...metric, value: 19.96, direction: null })).toBe(
      'Mean depth 19.96x (Warning; warning 20x, fail 10x)',
    );
    expect(
      describeBreachedMetric({
        label: 'Allele drop-out (ADO)',
        value: 32.14,
        unit: '%',
        verdict: 'fail',
        warnValue: 20,
        errorValue: 30,
        direction: 'higher_is_worse',
      }),
    ).toBe('Allele drop-out (ADO) 32.1% (Fail; warning 20%, fail 30%)');
  });
});

describe('the report page’s views', () => {
  it('accepts only a positive whole number as a signed version', () => {
    expect(parseSignedVersionParam('2')).toBe(2);
    expect(parseSignedVersionParam('12')).toBe(12);
    for (const value of ['0', '-1', '1.5', 'abc', '', '2x', null]) {
      expect(parseSignedVersionParam(value)).toBeNull();
    }
  });

  it('switches view and keeps the project', () => {
    expect(reportViewSearch('?project_id=P1', { version: 3 })).toBe('?project_id=P1&version=3');
    expect(reportViewSearch('?project_id=P1&version=3', 'live')).toBe('?project_id=P1&view=live');
    expect(reportViewSearch('?view=live', { version: 1 })).toBe('?version=1');
  });
});

// What the analysis looked at, frozen since 2026-10-14 (signed_record_sections.py).
describe('what the analysis looked at', () => {
  it('reads each section the record holds, and words it', () => {
    const record = parseSignedReport({ ...SNAPSHOT, ...SIGNED_ANALYSIS })!;
    expect(record.statement).toBe(ANALYTICAL_SIGN_OFF_STATEMENT);

    expect(record.variantDescriptions.state).toBe('recorded');
    if (record.variantDescriptions.state !== 'recorded') return;
    // An entry that is not one is left out; the downloaded record keeps it as stored.
    expect(record.variantDescriptions.value.small).toHaveLength(1);
    const [small] = record.variantDescriptions.value.small;
    expect(describeSmallVariantDescription(small)).toBe('GENE_PATH c.10A>G p.(Lys4Arg), missense variant (MODERATE)');
    expect(describeSmallVariantLocus(small)).toBe(
      '17:43,000,000 A → G · transcript ENST01 (MANE Select) · callset clair3',
    );
    expect(small.genotypes.map(describeGenotype)).toEqual([
      'MOTHER 0/0 (depth 28, GQ 60)',
      'PROBAND 0/1 (depth 30, AD 15,15, VAF 0.5)',
    ]);
    const [sv] = record.variantDescriptions.value.structural;
    expect(describeStructuralVariantDescription(sv)).toBe(
      'Deletion (copy loss), 2:1,000-50,000, 49,000 bp, genes GENE_DEL, 2p25.3, caller needlr',
    );
    expect(describeGenotype(sv.genotypes[0])).toBe('PROBAND 0/1 (copy number 1, 18 supporting reads)');

    expect(record.genePanels.state).toBe('recorded');
    if (record.genePanels.state === 'recorded') {
      expect(describeGenePanel(record.genePanels.value[0])).toBe(
        'Epilepsy, version 2, 120 genes; PanelApp panel 402, version 3.1 (confidence levels 3), imported 2026-09-01 08:00 UTC; first applied 2026-10-10 09:00 UTC by analyst',
      );
    }

    expect(record.referenceData.state).toBe('recorded');
    if (record.referenceData.state === 'recorded') {
      const { geneReference, clinicalCnvs } = record.referenceData.value;
      expect(geneReference.state).toBe('recorded');
      if (geneReference.state === 'recorded') {
        // A partial refresh left two ClinGen dosage releases: both are named.
        expect(describeGeneReferenceReleases(geneReference.value)).toBe(
          'ClinGen dosage 2026-09-30 and 2026-08-30 · HGNC 2026-09-01',
        );
        expect(geneReference.value.notInReference).toEqual(['NOT_LOADED']);
      }
      expect(clinicalCnvs.state).toBe('recorded');
      if (clinicalCnvs.state === 'recorded') {
        expect(describeCnvKnowledgebase(clinicalCnvs.value)).toBe(
          'clinical-cnv-kb, loaded 2026-10-01 09:00 UTC (release not stated)',
        );
      }
    }

    expect(record.repeatExpansions.state).toBe('recorded');
    if (record.repeatExpansions.state === 'recorded') {
      const [locus] = record.repeatExpansions.value.loci;
      expect(describeRepeatThresholds(locus)).toBe('grey zone from 27, pathogenic from 36');
      expect(describeRepeatCall(locus.calls[0])).toBe('PROBAND 0/1: 18, 40 repeats — Pathogenic');
      expect(describeCatalogue(record.repeatExpansions.value.catalogue, 'loci')).toBe(
        '96 loci (CoGA built-in, STRchive), digest abcdef012345',
      );
    }

    expect(record.paraphase.state).toBe('recorded');
    if (record.paraphase.state === 'recorded') {
      const [smn, gba] = record.paraphase.value.genes;
      expect(smn.statusRule).toBe('status by smn1_cn: pathogenic = 0, carrier = 1, normal ≥ 2');
      expect(smn.results[0]).toEqual({
        sample: 'PROBAND',
        status: 'carrier',
        copyNumberSignal: true,
        copyNumber: [{ label: 'SMN1 CN', value: 1 }],
      });
      expect(gba.statusRule).toBeNull();
    }
  });

  it('reads a record signed before these sections as not holding them', () => {
    const record = parseSignedReport(SNAPSHOT)!;
    expect(record.statement).toBeNull();
    for (const section of [
      record.variantDescriptions,
      record.genePanels,
      record.referenceData,
      record.repeatExpansions,
      record.paraphase,
    ]) {
      expect(section).toEqual({ state: 'absent' });
    }
  });

  it('reads a section that could not be read at sign-out as not captured, with its reason', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      ...SIGNED_ANALYSIS,
      paraphase: { unavailable: 'The Paraphase results could not be read.' },
      gene_panels: 'not an object',
      reference_data: {
        gene_reference: { unavailable: "The gene reference's releases could not be read." },
        clinical_cnvs: null,
      },
    })!;
    expect(record.paraphase).toEqual({ state: 'unavailable', reason: 'The Paraphase results could not be read.' });
    expect(record.genePanels.state).toBe('unavailable');
    expect(record.referenceData.state).toBe('recorded');
    if (record.referenceData.state === 'recorded') {
      expect(record.referenceData.value.geneReference).toEqual({
        state: 'unavailable',
        reason: "The gene reference's releases could not be read.",
      });
      // None loaded is recorded, as none.
      expect(record.referenceData.value.clinicalCnvs).toEqual({ state: 'recorded', value: null });
      expect(describeCnvKnowledgebase(null)).toBe('none loaded');
    }
  });

  it('names the statuses as the repeat and Paraphase pages do', () => {
    expect(paraphaseStatusWords('no_call')).toBe('No-call');
    expect(paraphaseStatusWords('none')).toBe('No change');
    expect(paraphaseStatusWords(null)).toBe('Not assessed');
  });
});

// Each repeat call's near-boundary warning and each locus's QA record of TRGT's counting units,
// frozen with the calls (signed_record_sections.repeat_expansion_calls, repeat_count_units.py).
describe('the repeat calls’ near-boundary warnings', () => {
  const repeatsOf = (snapshot: Record<string, unknown>): SignedRepeatExpansions => {
    const section = parseSignedReport(snapshot)!.repeatExpansions;
    expect(section.state).toBe('recorded');
    if (section.state !== 'recorded') throw new Error('not recorded');
    return section.value;
  };

  it('reads each allele’s warning, each locus’s QA record and the margin', () => {
    const repeats = repeatsOf({ ...SNAPSHOT, ...SIGNED_ANALYSIS });
    expect(repeats.countUnitMargin).toBe(3);
    const [htt, fmr1] = repeats.loci;
    expect(htt.countUnits).toEqual({
      validated: true,
      reason: 'Validation report 7',
      updatedBy: 'qa-lead',
      updatedAt: '2026-10-15T09:00:00+00:00',
    });
    expect(htt.calls[0].alleles.map((allele) => allele.countUnitWarning)).toEqual([null, null]);
    expect(fmr1.countUnits).toEqual({ validated: false, reason: null, updatedBy: null, updatedAt: null });
    expect(fmr1.calls[0].alleles.map((allele) => allele.countUnitWarning)).toEqual([
      null,
      { repeatCount: 53, margin: 3, boundaries: [{ repeatCount: 55, status: 'premutation' }], unreadableBoundaries: 0 },
    ]);
  });

  it('says the warning beside the call in the repeat page’s words, and each locus’s record', () => {
    const [htt, fmr1] = repeatsOf({ ...SNAPSHOT, ...SIGNED_ANALYSIS }).loci;
    expect(describeRepeatCall(fmr1.calls[0])).toBe(
      'MOTHER 1/2: 30, 53 repeats — Grey zone ⚠ Near a boundary: 53 is within 3 repeats of 55 (Premutation)',
    );
    // As the repeat page words the same warning.
    const [, frozen] = fmr1.calls[0].alleles;
    expect(describeSignedCountUnitWarning(frozen.countUnitWarning!)).toBe(
      describeCountUnitWarning({ repeat_count: 53, margin: 3, boundaries: [{ repeat_count: 55, status: 'premutation' }] }),
    );
    expect(describeRepeatLocusCalls(fmr1)).toBe(
      'MOTHER 1/2: 30, 53 repeats — Grey zone ⚠ Near a boundary: 53 is within 3 repeats of 55 (Premutation). TRGT’s counting units: not validated (no QA record).',
    );
    expect(describeRepeatLocusCalls(htt)).toBe(
      'PROBAND 0/1: 18, 40 repeats — Pathogenic. TRGT’s counting units: validated — Validation report 7 (qa-lead, 2026-10-15 09:00 UTC).',
    );
    // A validation QA withdrew: its reason, by whom and when.
    expect(
      describeCountUnits({
        validated: false,
        reason: 'New TRGT catalogue',
        updatedBy: 'qa-lead',
        updatedAt: '2026-10-21T08:00:00+00:00',
      }),
    ).toBe('not validated — New TRGT catalogue (qa-lead, 2026-10-21 08:00 UTC)');
    expect(describeCountUnitMargin(3)).toBe(
      'While QA had not validated TRGT’s counting units at a locus, a count within 3 repeats of a classification boundary was marked ⚠ Near a boundary: a difference of that size between TRGT’s count and the clinical repeat number could change its status. The status stands as classified.',
    );
  });

  it('reads a record signed before the warnings as it was signed', () => {
    const repeats = repeatsOf({
      ...SNAPSHOT,
      ...SIGNED_ANALYSIS,
      repeat_expansions: SIGNED_REPEAT_EXPANSIONS_BEFORE_COUNT_UNITS,
    });
    expect(repeats.countUnitMargin).toBeNull();
    expect(repeats.loci.map((locus) => locus.countUnits)).toEqual([null, null]);
    expect(
      repeats.loci.flatMap((locus) => locus.calls.flatMap((call) => call.alleles.map((allele) => allele.countUnitWarning))),
    ).toEqual([null, null, null, null]);
    // Its calls read as they always did: no warning, nothing of QA's records.
    expect(repeats.loci.map(describeRepeatLocusCalls)).toEqual([
      'PROBAND 0/1: 18, 40 repeats — Pathogenic',
      'MOTHER 1/2: 30, 53 repeats — Grey zone',
    ]);
  });

  it('keeps a warning or a record it cannot fully read, and says so', () => {
    const repeats = repeatsOf({
      ...SNAPSHOT,
      repeat_expansions: {
        count_unit_margin: 'three',
        loci: [
          {
            locus_id: 'L1',
            count_units: { validated: 'yes', reason: '  ', updated_by: 7 },
            calls: [
              {
                sample: 'S1',
                alleles: [
                  {
                    repeat_count: 53,
                    count_unit_warning: {
                      repeat_count: 53,
                      margin: 3,
                      boundaries: [{ repeat_count: 55, status: 'premutation' }, { repeat_count: 52 }, 'a boundary'],
                    },
                  },
                  { repeat_count: 12, count_unit_warning: { boundaries: 'none' } },
                  { repeat_count: 40, count_unit_warning: 'not a warning' },
                ],
              },
            ],
          },
          { locus_id: 'L2', count_units: 'not a record', calls: [] },
        ],
      },
    });
    expect(repeats.countUnitMargin).toBeNull();
    const [l1, l2] = repeats.loci;
    // Only `true` is validated; a reason that is blank is none.
    expect(l1.countUnits).toEqual({ validated: false, reason: null, updatedBy: null, updatedAt: null });
    expect(l2.countUnits).toBeNull();
    const [kept, bare, none] = l1.calls[0].alleles.map((allele) => allele.countUnitWarning);
    expect(kept).toEqual({
      repeatCount: 53,
      margin: 3,
      boundaries: [{ repeatCount: 55, status: 'premutation' }],
      unreadableBoundaries: 2,
    });
    expect(describeSignedCountUnitWarning(kept!)).toBe(
      '53 is within 3 repeats of 55 (Premutation) and 2 boundaries this view cannot read',
    );
    expect(describeSignedCountUnitWarning(bare!)).toBe('a count is within ? repeats of a boundary');
    expect(
      describeSignedCountUnitWarning({ repeatCount: 11, margin: 3, boundaries: [], unreadableBoundaries: 1 }),
    ).toBe('11 is within 3 repeats of a boundary this view cannot read');
    expect(none).toBeNull();
    // A locus without calls says its record alone.
    expect(describeRepeatLocusCalls({ ...l1, calls: [] })).toBe('TRGT’s counting units: not validated (no QA record).');
  });
});

// A list in a section that this view cannot read (TRACE-17): read as empty, or its entry left
// out, it read as if the section held nothing there.
describe('a section’s lists this view cannot read', () => {
  it('counts each list that is no list, and each entry not of its list’s kind', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      ...SIGNED_ANALYSIS,
      gene_panels: { applied: 'not a list' },
      reference_data: {
        ...SIGNED_ANALYSIS.reference_data,
        gene_reference: { ...SIGNED_ANALYSIS.reference_data.gene_reference, genes: 'GENE1', not_in_reference: [7] },
      },
    })!;
    expect(record.genePanels).toEqual({ state: 'recorded', value: [], malformed: 1 });
    // A part's lists count for the part and for the section that holds it.
    expect(record.referenceData).toMatchObject({ state: 'recorded', malformed: 2 });
    if (record.referenceData.state === 'recorded') {
      expect(record.referenceData.value.geneReference).toMatchObject({ state: 'recorded', malformed: 2 });
      expect(record.referenceData.value.clinicalCnvs).not.toHaveProperty('malformed');
    }
    // The fixture's entries that are not of their list's kind: a variant description, a track.
    expect(record.variantDescriptions).toMatchObject({ state: 'recorded', malformed: 1 });
    expect(record.coverageTracks).toMatchObject({ state: 'recorded', malformed: 1 });
    // A section read in full counts none.
    expect(record.paraphase.state).toBe('recorded');
    expect(record.paraphase).not.toHaveProperty('malformed');
  });

  it('counts a missing number as no malformed entry, and a word among numbers as one', () => {
    const descriptions = (genotype: Record<string, unknown>) =>
      parseSignedReport({
        ...SNAPSHOT,
        ...SIGNED_ANALYSIS,
        variant_descriptions: {
          small: [{ ...(SIGNED_ANALYSIS.variant_descriptions.small[0] as Record<string, unknown>), genotypes: [genotype] }],
          structural: [],
        },
      })!.variantDescriptions;
    expect(descriptions({ sample: 'PROBAND', gt: '0/1', ad: [15, null] })).not.toHaveProperty('malformed');
    expect(descriptions({ sample: 'PROBAND', gt: '0/1', ad: [15, 'x'] })).toMatchObject({ malformed: 1 });
    expect(descriptions({ sample: 'PROBAND', gt: '0/1', ad: '15,15' })).toMatchObject({ malformed: 1 });
  });
});

// What each coverage track held, frozen since 2026-10-20 (signed_record_sections.coverage_tracks):
// which kind of coverage a depth check rested on (TRACE-9).
describe('what each coverage track held', () => {
  const tracksOf = (snapshot: Record<string, unknown>): SignedCoverageTrack[] => {
    const section = parseSignedReport(snapshot)!.coverageTracks;
    expect(section.state).toBe('recorded');
    return section.state === 'recorded' ? section.value : [];
  };

  it('reads each track the record holds, per sample and caller, with where it came from', () => {
    const tracks = tracksOf({ ...SNAPSHOT, ...SIGNED_ANALYSIS });
    // An entry that is not one is left out; the downloaded record keeps it as stored.
    expect(tracks).toEqual([
      {
        sample: 'MOTHER',
        source: 'web',
        content: 'unknown',
        files: [{ file: 'MOTHER.bed', rows: 2, loadedAt: '2026-10-01T09:00:00+00:00', loadedFrom: 'package' }],
      },
      {
        sample: 'PROBAND',
        source: 'web',
        content: 'read_depth',
        files: [{ file: 'PROBAND.upload.bed', rows: 1200, loadedAt: '2026-10-02T09:00:00+00:00', loadedFrom: 'upload' }],
      },
      {
        sample: 'PROBAND',
        source: 'wisecondorx',
        content: 'log2_ratio',
        files: [{ file: 'PROBAND_bins.bed', rows: 3000, loadedAt: '2026-10-01T09:00:00+00:00', loadedFrom: 'package' }],
      },
    ]);
  });

  it('words each track plainly, as the signed view and its printout list them', () => {
    const tracks = tracksOf({ ...SNAPSHOT, ...SIGNED_ANALYSIS });
    expect(tracks.map(describeCoverageTrack)).toEqual([
      'MOTHER coverage BED (package) — unknown',
      'PROBAND coverage BED (upload) — read depth',
      'PROBAND WisecondorX (package) — log2 ratio',
    ]);
    expect(describeCoverageTracks(tracks)).toBe(
      'MOTHER coverage BED (package) — unknown; PROBAND coverage BED (upload) — read depth; PROBAND WisecondorX (package) — log2 ratio',
    );
    expect(describeCoverageTracks([])).toBe('none');
    expect(tracks.map(describeCoverageContent)).toEqual([
      'unknown: it declares neither read depth nor a log2 ratio, so it is not read as depth',
      'read depth',
      'log2 ratio, not read as depth',
    ]);
    expect(describeCoverageFiles(tracks[1])).toBe(
      'PROBAND.upload.bed, from a single-file upload, 1,200 rows, loaded 2026-10-02 09:00 UTC',
    );
    expect(describeCoverageFiles(tracks[0])).toBe('MOTHER.bed, from a package import, 2 rows, loaded 2026-10-01 09:00 UTC');
    expect(coverageTrackCaller('hificnv')).toBe('HiFiCNV');
    expect(coverageTrackCaller('cnvkit')).toBe('cnvkit');
  });

  it('reads what it cannot be sure of as unknown or not recorded, never as depth', () => {
    const [unknownCaller, odd, unrecorded] = tracksOf({
      ...SNAPSHOT,
      coverage_tracks: {
        tracks: [
          { sample: 'S1', source: 'cnvkit', content: 'unknown', files: [] },
          // A value that is neither read depth nor a log2 ratio is read as unknown.
          { sample: 'S1', source: 'web', content: 'depth', files: [{ file: 'S1.bed', loaded_from: 'demo-script' }] },
          // A track stored before its loader recorded where it came from.
          { sample: 'S2', source: 'web', content: 'unknown', files: [{ file: null, rows: null, loaded_at: null, loaded_from: null }] },
        ],
      },
    });
    expect(describeCoverageContent(unknownCaller)).toBe('unknown: what it holds is not recorded, so it is not read as depth');
    expect(describeCoverageFiles(unknownCaller)).toBe('no file recorded');
    expect(odd.content).toBe('unknown');
    expect(describeCoverageTrack(odd)).toBe('S1 coverage BED (demo-script) — unknown');
    expect(describeCoverageFiles(odd)).toBe('S1.bed, from demo-script');
    expect(describeCoverageTrack(unrecorded)).toBe('S2 coverage BED (origin not recorded) — unknown');
    expect(describeCoverageFiles(unrecorded)).toBe('file not named, origin not recorded');
    // A family without a coverage track holds the section, as none.
    expect(tracksOf({ ...SNAPSHOT, coverage_tracks: { tracks: [] } })).toEqual([]);
  });

  it('reads a record signed before the section as not holding it, and one that could not capture it as unavailable', () => {
    expect(parseSignedReport(SNAPSHOT)!.coverageTracks).toEqual({ state: 'absent' });
    expect(
      parseSignedReport({ ...SNAPSHOT, coverage_tracks: { unavailable: 'The coverage tracks could not be read.' } })!
        .coverageTracks,
    ).toEqual({ state: 'unavailable', reason: 'The coverage tracks could not be read.' });
    // The check names the section, when it changed since, in words.
    expect(describeReportSections(['coverage_tracks', 'paraphase'])).toBe('coverage tracks and Paraphase results');
  });
});

// The sign-out check lists what the signed version does not hold, and so could not compare
// (not_compared): a section, a reference module, or a part CoGA records since. The pages name
// it beside "still matches" and beside what changed (TRACE-19).
describe('what the sign-out check could not compare', () => {
  it('names each section, module and part in words', () => {
    expect(describeNotCompared(['nipt', 'coverage_tracks'])).toBe('NIPT results and coverage tracks');
    expect(describeNotCompared(['repeat_expansions.count_units'])).toBe(
      'the repeat calls’ near-boundary warnings and QA’s records of their counting units',
    );
    expect(describeNotCompared(['pgt_analysis.qc_verdict', 'pgt_analysis.inputs.blocks_outdated'])).toBe(
      'the embryos’ QC verdicts and whether the haplotype blocks were built for the family’s pedigree',
    );
    // A reference module CoGA records since: by its name, else by its key.
    expect(describeNotCompared(['modules.hpo', 'modules.gene_loci', 'modules.gencc'])).toBe(
      'the HPO version, the gene loci’s source and the gencc version',
    );
    // A part the page has no words for yet, under its section's name.
    expect(describeNotCompared(['pgt_analysis.inputs.new_flag'])).toBe('PGT embryo results (inputs new flag)');
    expect(describeNotCompared(['some_new_section'])).toBe('some_new_section');
  });

  it('says so in one sentence, or not at all', () => {
    expect(notComparedSentence([], 'signed version 2')).toBeNull();
    expect(notComparedSentence(undefined, 'signed version 2')).toBeNull();
    expect(notComparedSentence(['repeat_expansions.count_units', 'modules.hpo'], 'signed version 2')).toBe(
      'Not in signed version 2, so not compared: the repeat calls’ near-boundary warnings and QA’s records of their counting units and the HPO version.',
    );
  });
});

// A PGT family's embryo calls at the ROI, frozen since 2026-10-20 (pgt_analysis.py): the calls
// the family page showed, each with what it rests on.
describe('the PGT embryo results', () => {
  const pgtRecord = (pgtAnalysis: unknown) =>
    parseSignedReport({ ...SNAPSHOT, ...SIGNED_ANALYSIS, pgt_analysis: pgtAnalysis })!;
  const recorded = (record: SignedReport): SignedPgtAnalysis => {
    if (record.pgtAnalysis.state !== 'recorded') throw new Error('not recorded');
    return record.pgtAnalysis.value;
  };

  it('reads each embryo call with what it rests on', () => {
    const analysis = recorded(pgtRecord(SIGNED_PGT_ANALYSIS));

    expect(analysis.roi).toEqual({ query: 'GENE1', label: 'GENE1', chrom: '7', start: 30500000, end: 30600000 });
    expect(analysis.inheritance).toEqual({ recorded: null, applied: 'AD' });
    expect(analysis.phaseCorrections).toEqual([SIGNED_PGT_ANALYSIS.phase_corrections[0]]);
    // An entry that is not an embryo's call reads as an embryo not assessed, never as a call.
    expect(analysis.embryos.map((embryo) => [embryo.sample, embryo.call])).toEqual([
      ['E1', 'affected_or_at_risk'],
      ['E2', 'uninformative'],
      ['E3', 'unaffected_non_carrier'],
      ['an embryo', null],
    ]);
    const [e1, e2] = analysis.embryos;
    expect(e1.qc).toEqual({ informativeSites: 40, mendelErrors: 1, mendelRate: 0.025, paternalMarkers: 22, maternalMarkers: 18 });
    expect(e2).toMatchObject({ roiNotCovered: true, recombinations: [30550000], qc: null });
    expect(analysis.sources.map((source) => source.sample)).toEqual(['E1', 'FATHER']);
    expect(analysis).toMatchObject({ blocksRead: 6, blocksDigest: 'abcdef0123456789', markersTruncated: false, recombinationFlank: 250000 });
  });

  it('words the calls as the family page and the haplotype track do', () => {
    const analysis = recorded(pgtRecord(SIGNED_PGT_ANALYSIS));
    const [e1, e2, e3, unread] = analysis.embryos;

    expect(describeDiseaseHaplotypes(analysis)).toBe('P2 (dominant)');
    expect(describePgtInheritance(analysis)).toBe('not recorded; CoGA assumed AD');
    expect(describeEmbryoCall(e1)).toBe('Affected / at risk');
    expect(describeEmbryoCall(unread)).toBe('Not assessed');
    expect(describeInherited(e1.inherited)).toBe('M1 and P2 (29,000,000–32,000,000)');
    expect(describeInherited(e3.inherited)).toBe('M2 and P1 (29,000,000–32,000,000)');
    expect(describeInherited([])).toBeNull();
    expect(describeEmbryoQc(e1.qc!)).toBe(
      '40 informative sites, Mendelian error rate 2.5% (1 of 40); informative markers 22 paternal, 18 maternal',
    );
    expect(describeNoCall(e1, analysis)).toBeNull();
    expect(describeNoCall(e2, analysis)).toBe(
      'This embryo’s haplotype does not cover the ROI on the parental side the call needs (missing data), so no call can be made.',
    );
    expect(describePgtMembers(analysis.members)).toBe(
      'E1 (embryo), E2 (embryo), E3 (embryo), FATHER (father, affected), INDEX (proband, affected), MOTHER (mother, not a carrier)',
    );
    expect(describePgtInputs(analysis)).toBe(
      'the haplotype blocks of family.vcf.gz, loaded 2026-10-01 09:00 UTC (E1 and FATHER); 6 blocks read, digest abcdef012345',
    );
    expect(describeReportSections(['pgt_analysis'])).toBe('PGT embryo results');
  });

  it('says the embryos were not called from blocks built for another pedigree (CLIN-66)', () => {
    const outdated = recorded(
      pgtRecord({
        ...SIGNED_PGT_ANALYSIS,
        disease_haplotype_resolved: false,
        disease_haplotypes: [],
        inputs: { ...SIGNED_PGT_ANALYSIS.inputs, blocks_outdated: true },
      }),
    );
    const [, e2] = outdated.embryos;
    expect(outdated.blocksOutdated).toBe(true);
    expect(describeNoCall(e2, outdated)).toBe(
      "The haplotype blocks were built for another pedigree or other sexes than the family's, so no embryo is called from them: import the phased genotypes again.",
    );
    // A record signed before the check holds none: read as current blocks.
    expect(recorded(pgtRecord(SIGNED_PGT_ANALYSIS)).blocksOutdated).toBe(false);
  });

  it('says why an embryo is not called: no disease haplotype, or its unrecorded sex', () => {
    const unresolved = recorded(
      pgtRecord({ ...SIGNED_PGT_ANALYSIS, disease_haplotype_resolved: false, disease_haplotypes: [] }),
    );
    const [, , e3] = unresolved.embryos;
    expect(describeDiseaseHaplotypes(unresolved)).toBeNull();
    expect(describeNoCall({ ...e3, call: 'uninformative' }, unresolved)).toBe(
      'No disease haplotype could be resolved at the ROI (uninformative markers).',
    );
    const sexDependent = { ...e3, call: 'uninformative' as const, sexDependent: { ifMale: 'unaffected_non_carrier' as const, ifFemale: 'carrier' as const } };
    expect(describeNoCall(sexDependent, unresolved)).toBe(
      "This embryo's sex is not recorded, and the call depends on it: unaffected if male, carrier if female. Record the sex to resolve it.",
    );
    expect(homologLabel({ origin: 'maternal', homolog: '?' })).toBe('M?');
    expect(homologLabel({ origin: null, homolog: '0' })).toBe('?1');
    expect(describePgtInheritance({ ...unresolved, inheritance: { recorded: 'XLR', applied: 'XLR' } })).toBe('XLR');
    expect(describePgtInputs({ ...unresolved, sources: [], blocksRead: null })).toBe('no recorded haplotype file');
    expect(describePgtMembers([])).toBeNull();
  });

  // The owner's decision of 2026-10-20: an embryo call whose phasing QC at the ROI does not meet
  // its thresholds carries a warning, and the record freezes the verdict and the thresholds.
  it("reads each embryo's QC verdict and the thresholds it was checked against", () => {
    const analysis = recorded(pgtRecord(SIGNED_PGT_ANALYSIS));
    const [e1, e2, e3, unread] = analysis.embryos;

    expect(analysis.qcThresholds).toEqual({ minInformativeSites: 50, maxMendelRate: 0.02, minMarkersPerSide: 6 });
    expect(e1.qcVerdict).toEqual({
      status: 'warning',
      sides: ['paternal'],
      findings: [
        { measure: 'informative_sites', value: 40, threshold: 50 },
        { measure: 'mendel_rate', value: 0.025, threshold: 0.02 },
      ],
      unreadableFindings: 0,
    });
    expect(e2.qcVerdict).toEqual({ status: 'not_counted', sides: ['paternal'], findings: [], unreadableFindings: 0 });
    expect(e3.qcVerdict?.status).toBe('pass');
    expect(unread.qcVerdict).toBeNull();

    expect(describeQcThresholds(analysis.qcThresholds!)).toBe(
      'at least 50 informative sites, a Mendelian error rate of at most 2% and at least 6 markers on each parental side the call reads',
    );
    expect(describeSignedQcVerdict(e1.qcVerdict!)).toBe(
      "⚠ The phasing QC at the ROI did not meet its thresholds (markers checked on the father's side): 40 informative sites (fewer than 50); Mendelian error rate 2.5% (above 2%).",
    );
    expect(describeSignedQcVerdict(e2.qcVerdict!)).toBe('⚠ The call was not checked against the QC thresholds.');
    expect(describeSignedQcVerdict(e3.qcVerdict!)).toBe(
      "The phasing QC at the ROI met its thresholds (markers checked on the father's side).",
    );
  });

  it('keeps a verdict measure it cannot read, and says so', () => {
    const analysis = recorded(
      pgtRecord({
        ...SIGNED_PGT_ANALYSIS,
        embryos: [
          {
            sample: 'E1',
            call: 'carrier',
            qc_verdict: {
              status: 'warning',
              sides: ['paternal', 'maternal', 'a side'],
              findings: [{ measure: 'maternal_markers', value: 2, threshold: 6 }, { measure: 'a measure', value: 1 }, 'not a finding'],
            },
          },
          { sample: 'E2', call: 'carrier', qc_verdict: { status: 'a status' } },
        ],
        rules: { qc_thresholds: { min_informative_sites: 50 } },
      }),
    );
    const [e1, e2] = analysis.embryos;
    expect(e1.qcVerdict).toMatchObject({ sides: ['paternal', 'maternal'], unreadableFindings: 2 });
    expect(describeSignedQcVerdict(e1.qcVerdict!)).toBe(
      "⚠ The phasing QC at the ROI did not meet its thresholds (markers checked on both parents' sides): 2 markers on the mother's side (fewer than 6). 2 measures of it cannot be read here; the downloaded record holds them as stored.",
    );
    expect(e2.qcVerdict).toBeNull();
    expect(describeQcThresholds(analysis.qcThresholds!)).toBe('at least 50 informative sites');
    expect(describeQcThresholds({ minInformativeSites: null, maxMendelRate: null, minMarkersPerSide: null })).toBe(
      'thresholds the record does not state',
    );
  });

  it('reads a PGT record signed before the QC verdict as before: no verdict, no thresholds', () => {
    const analysis = recorded(pgtRecord(SIGNED_PGT_ANALYSIS_BEFORE_QC_VERDICT));
    expect(analysis.qcThresholds).toBeNull();
    expect(analysis.embryos.map((embryo) => embryo.qcVerdict)).toEqual([null, null, null, null]);
    // The rest reads as it did.
    expect(analysis.embryos.map((embryo) => embryo.call)).toEqual([
      'affected_or_at_risk',
      'uninformative',
      'unaffected_non_carrier',
      null,
    ]);
  });

  it('reads a record without the section as absent, and one that could not capture it as unavailable', () => {
    expect(parseSignedReport(SNAPSHOT)!.pgtAnalysis).toEqual({ state: 'absent' });
    expect(pgtRecord({ unavailable: "The PGT analysis's embryo results could not be read." }).pgtAnalysis).toEqual({
      state: 'unavailable',
      reason: "The PGT analysis's embryo results could not be read.",
    });
    // The Sample QC says which application read the family: a PGT family's older record is told apart.
    expect(parseSignedReport({ ...SNAPSHOT, sample_qc: { overall_status: 'pass', application: 'pgt' } })!.sampleQc?.application).toBe('pgt');
  });
});

// A monogenic NIPT family's record holds its NIPT results (signed_record_nipt); no other
// family's does, and the signed view then says nothing of them.
describe('a NIPT family’s NIPT results', () => {
  const NIPT = {
    samples: { father: 'FATHER1', maternal_plasma: 'CFDNA1', fetus: null },
    fetal_fraction: {
      ff: 0.0,
      ff_computed: null,
      ff_median: 0.05,
      ci_low: null,
      ci_high: null,
      n_sites: 3,
      method: 'category7_pooled',
      low_confidence: true,
      status: 'fail',
      message: 'Not estimated, from 3 category-7 sites (an estimate needs at least 5).',
    },
    de_novo_window: null,
    category_counts: { '7': 3, '1': 0, '10': 'not a count', x: 2 },
    filter_counts: { total_in: 10, failed_quality: 2, failed_artifact: 0, passed: 8, paternal_only: 2 },
    quality_failures: { low_vaf: 2, few_alt_reads: 1 },
    checks: {
      paternity: { status: 'warn', message: 'Too few paternal-informative sites.' },
      fetal_sex: { call: 'indeterminate', status: 'warn', message: 'Indeterminate.', paternal_x: 'indeterminate', chry: null },
      plasma_sample: { status: 'fail', message: 'Male DNA.' },
    },
    target_coverage: null,
    variants: [
      {
        variant_id: '7-400-C-T',
        source: 'nipt',
        genes: ['GENEB'],
        category: null,
        category_label: 'undetermined',
        maternal_state: 'hom_ref',
        fetal_inheritance: 'paternal_not_transmitted',
        expected_vaf: 0.1,
        observed_vaf: 0,
        confidence: 0.99,
        paternal_transmission_probability: 0.000001,
        flags: [],
        plasma: { alt_reads: 0, depth: 1500, depth_estimated: true },
        father: { state: 'het', vaf: 0.5, depth: 300 },
        de_novo: null,
      },
      'not an entry',
    ],
    not_classified: [{ variant_id: '7-300-A-G', reason: 'artifact_list' }, { reason: 'no_plasma_call' }],
    recessive_genes: [{ gene: 'GENEB', risk: 0.0001, risk_uses_prior: true }],
    inputs: { target_tables: [], artifact_list: null },
    model: {},
  };

  it('reads the section the record holds', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      sample_qc: { ...SNAPSHOT.sample_qc, application: 'nipt' },
      nipt: NIPT,
    })!;
    expect(isNiptRecord(record)).toBe(true);
    expect(record.sampleQc?.application).toBe('nipt');
    expect(record.nipt.state).toBe('recorded');
    if (record.nipt.state !== 'recorded') return;
    const nipt = record.nipt.value;
    expect(nipt.samples).toEqual({ father: 'FATHER1', maternalPlasma: 'CFDNA1', fetus: null });
    expect(nipt.fetalFraction).toMatchObject({ status: 'fail', ffComputed: null, nSites: 3, lowConfidence: true });
    // Counts only, in category order; a count that is not a number is left out.
    expect(nipt.categoryCounts).toEqual([
      [1, 0],
      [7, 3],
    ]);
    expect(nipt.qualityFailures).toEqual([
      ['few_alt_reads', 1],
      ['low_vaf', 2],
    ]);
    expect(nipt.plasmaSample).toEqual({ status: 'fail', message: 'Male DNA.' });
    expect(nipt.fetalSex).toMatchObject({ call: 'indeterminate', status: 'warn', paternalX: 'indeterminate', chry: null });
    expect(nipt.targetCoverage).toBeNull();
    // An entry that cannot be read is skipped, never shown as a variant.
    expect(nipt.variants).toHaveLength(1);
    expect(nipt.variants[0]).toMatchObject({
      variantId: '7-400-C-T',
      category: null,
      plasma: { altReads: 0, depth: 1500, depthEstimated: true },
      father: { state: 'het', vaf: 0.5, depth: 300 },
      deNovo: null,
    });
    expect(nipt.notClassified).toEqual([
      { variantId: '7-300-A-G', reason: 'artifact_list' },
      { variantId: 'a variant', reason: 'no_plasma_call' },
    ]);
    expect(nipt.recessiveGenes).toEqual([
      { gene: 'GENEB', risk: 0.0001, riskUsesPrior: true, maternalVariantId: null, paternalVariantId: null },
    ]);
    expect(nipt.artifactList).toBeNull();
    expect(nipt.modelReference).toBeNull();
  });

  it('reads a record without the section by whose it is', () => {
    // Another family's record: no NIPT results, and nothing to say of them.
    const other = parseSignedReport(SNAPSHOT)!;
    expect(other.nipt).toEqual({ state: 'absent' });
    expect(isNiptRecord(other)).toBe(false);
    // A NIPT family's record signed before CoGA froze them: absent, and said so.
    const before = parseSignedReport({ ...SNAPSHOT, sample_qc: { ...SNAPSHOT.sample_qc, application: 'nipt' } })!;
    expect(before.nipt).toEqual({ state: 'absent' });
    expect(isNiptRecord(before)).toBe(true);
    // Results that could not be read: not captured, with the reason.
    const unread = parseSignedReport({ ...SNAPSHOT, nipt: { unavailable: 'The NIPT results could not be read.' } })!;
    expect(unread.nipt).toEqual({ state: 'unavailable', reason: 'The NIPT results could not be read.' });
    expect(isNiptRecord(unread)).toBe(true);
  });

  it('words the results as the NIPT page does', () => {
    const record = parseSignedReport({ ...SNAPSHOT, nipt: NIPT })!;
    if (record.nipt.state !== 'recorded') throw new Error('not recorded');
    const nipt = record.nipt.value;
    expect(describeNiptFetalFraction(nipt.fetalFraction!)).toBe('not estimated, from 3 category-7 sites');
    expect(
      describeNiptFetalFraction({
        ...nipt.fetalFraction!,
        ffComputed: 0.12,
        ciLow: 0.109,
        ciHigh: 0.131,
        ffMedian: 0.118,
        nSites: 1,
      }),
    ).toBe('12.0% (95% CI 10.9%–13.1%) from 1 category-7 site; per-site median 11.8%');
    expect(describeNiptFetalSex(nipt.fetalSex!)).toBe(
      "Indeterminate (the father's X alleles read indeterminate; the plasma's chrY coverage reads nothing)",
    );
    expect(describeNiptFunnel(nipt.filterCounts)).toBe(
      '10 cfDNA calls: 2 quality-filtered, 0 artifact-filtered, 8 analysed; 2 paternal alleles without a plasma call of their own',
    );
    expect(describeNiptFunnel({ total_in: 1, passed: 1 })).toBe(
      '1 cfDNA calls: 0 quality-filtered, 0 artifact-filtered, 1 analysed',
    );
    expect(
      describeNiptCoverage({
        targets: 60000,
        medianMean: 800,
        belowCritical: 1500,
        belowAdvisory: 40000,
        incomplete: 1000,
        criticalMeanDepth: 300,
        weakGenes: [],
      }),
    ).toBe('median 800x over 60,000 targets; 1,500 below 300x, 1,000 with uncovered bases');
    expect(describeNiptVariant(nipt.variants[0])).toBe(
      'Unclassified (undetermined), confidence 0.99: maternal hom_ref, fetal paternal_not_transmitted; VAF 0.0% (expected 10.0%); plasma reads no call at ~1500× (target coverage); father het · 50.0% · 300×. paternal inherited 0.0%.',
    );
    expect(NIPT_NOT_CLASSIFIED_REASONS.artifact_list).toBe(
      'it is on the assay’s recurrent-artifact list, which the analysis leaves out',
    );
    // The sign-out check names a changed section in words.
    expect(describeReportSections(['nipt', 'sample_qc'])).toBe('NIPT results and sample-integrity QC');
  });
});

// The family's live report and the NIPT report say alike how they stand to the latest
// signed version (TF-06 H9): never the signed report.
describe('a live report beside the latest signed version', () => {
  const state = (overrides: Partial<Parameters<typeof liveReportSignedState>[0]>) =>
    liveReportSignedState({
      signoutsFailed: false,
      signoutsPending: false,
      latestVersion: 2,
      checkFailed: false,
      check: { version: 2, matches: true },
      ...overrides,
    });

  it('reads the state from the signed versions and the check', () => {
    expect(state({ signoutsFailed: true })).toBe('unknown');
    expect(state({ signoutsPending: true })).toBe('loading');
    expect(state({ latestVersion: null })).toBe('none');
    expect(state({ checkFailed: true })).toBe('unverified');
    expect(state({ check: undefined })).toBe('checking');
    expect(state({ check: { version: 1, matches: true } })).toBe('unverified');
    expect(state({ check: { version: 2, matches: null } })).toBe('unverified');
    expect(state({})).toBe('matches');
    expect(state({ check: { version: 2, matches: false } })).toBe('changed');
  });

  it('heads every printout with what it is', () => {
    expect(liveReportSignedNotice('none', null)).toBe('Draft — this report has not been signed.');
    expect(liveReportSignedNotice('matches', 2)).toBe(
      'Not the signed report — this is the live report. Print signed version 2 from its record.',
    );
    expect(liveReportSignedNotice('changed', 2)).toBe(
      'Not the signed report — the content differs from signed version 2.',
    );
    expect(liveReportSignedNotice('unverified', 2)).toBe(
      'Not verified against signed version 2 — do not use as the signed report.',
    );
    expect(liveReportSignedNotice('checking', 2)).toBe(
      'Not verified against signed version 2 — do not use as the signed report.',
    );
    expect(liveReportSignedNotice('unknown', null)).toBe(
      'The sign-out record could not be loaded — do not use as the signed report.',
    );
    expect(liveReportSignedNotice('loading', null)).toBe(
      'The sign-out record is still loading — do not use as the signed report.',
    );
  });
});
