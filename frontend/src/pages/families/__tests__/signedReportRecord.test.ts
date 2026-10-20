// REQ-TRACE-007: a signed version is rendered from its frozen snapshot alone. These pin how
// the snapshot is read: every section the record holds, a section an older record predates
// as absent (never as empty), and an entry that cannot be read kept, not dropped.
import { describe, expect, it } from 'vitest';

import {
  describeBreachedMetric,
  describeFrozenEvidenceValues,
  describeModuleVersions,
  describeSignedDrift,
  describeSignedStructuralDrift,
  describeSmallVariantEvidenceChange,
  describeStructuralEvidenceChange,
  parseSignedReport,
  parseSignedVersionParam,
  reportViewSearch,
} from '../signedReportRecord';

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
          { metric_key: 'mean_coverage', label: 'Mean coverage', unit: '×', value: 18, warn_value: 20, error_value: 10, verdict: 'warn' },
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
        breached: [{ label: 'Mean coverage', value: 18, unit: '×', verdict: 'warn', warnValue: 20, errorValue: 10 }],
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
    });

    // No ACMG criteria: the class its classification tag names; no evidence was frozen.
    expect(tagged.classification).toBe('VUS - class 3');
    expect(tagged.pointTotal).toBeNull();
    expect(tagged.criteria).toEqual([]);
    expect(tagged.evidence).toBeNull();

    expect(unreadable.variantId).toBeNull();
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
            consequence: { effect: 'stop_gained', impact: 'HIGH', lof: null },
            gene_constraint: { gene_missense_z: 3.521 },
          },
        },
      ],
    })!;
    const [variant] = record.reportedVariants!;

    expect(variant.evidence?.consequence).toEqual([
      { label: 'consequence', value: 'stop_gained' },
      { label: 'impact', value: 'HIGH' },
      { label: 'LoF', value: null },
    ]);
    expect(variant.evidence?.geneConstraint).toEqual([{ label: 'gnomAD missense Z', value: '3.52' }]);
    // The consequence first, read from the primary transcript; the missense Z is the gene's.
    expect(describeFrozenEvidenceValues(variant.evidence!)).toBe(
      'Read from its primary transcript ENST00000000001: consequence stop_gained, impact HIGH and LoF not ' +
        'annotated; population frequencies gnomAD AF 3.10e-5, gnomAD exomes AF 1.20e-5, gnomAD genomes AF ' +
        '3.10e-5, gnomAD popmax AF 0.0002, TOPMed AF not annotated, gnomAD allele count 4, gnomAD homozygotes 0 ' +
        'and gnomAD hemizygotes not annotated; in silico scores CADD 24.3, REVEL 0.412, SpliceAI 0.02, SIFT ' +
        'deleterious(0.01), PolyPhen probably_damaging(0.981), AlphaMissense class not annotated and ' +
        "AlphaMissense score not annotated. From its gene's profile: gnomAD missense Z 3.52.",
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
    expect(
      describeSmallVariantEvidenceChange(
        ['effect', 'impact', 'lof', 'revel', 'gene_missense_z', 'annotations'],
        { effect: 'missense_variant', impact: 'MODERATE', lof: null, revel: 0.412, gene_missense_z: 2.95 },
        { effect: 'stop_gained', impact: 'HIGH', lof: 'HC', revel: 0.951, gene_missense_z: 3.52 },
      ),
    ).toBe(
      'consequence missense_variant → stop_gained; impact MODERATE → HIGH; LoF n/a → HC; REVEL 0.412 → 0.951; ' +
        'gnomAD missense Z 2.95 → 3.52',
    );
    // Two values that differ past the two digits a missense Z is written with are written in full.
    expect(describeSmallVariantEvidenceChange(['gene_missense_z'], { gene_missense_z: 3.091 }, { gene_missense_z: 3.089 })).toBe(
      'gnomAD missense Z 3.091 → 3.089',
    );
  });

  it('reads a moved consequence, LoF call and gene missense Z in the drift a version was signed with', () => {
    const record = parseSignedReport({
      ...SNAPSHOT,
      drift: {
        checked: 1,
        drifted_count: 1,
        drifted: [
          {
            variant_id: '12-40100301-C-T',
            status: 'drifted',
            clinvar_from: 'uncertain_significance',
            clinvar_to: 'uncertain_significance',
            changed: ['effect', 'lof', 'gene_missense_z', 'annotations'],
            evidence_from: { effect: 'stop_gained', lof: 'LC', gene_missense_z: 2.95 },
            evidence_to: { effect: 'stop_gained&splice_region_variant', lof: 'HC', gene_missense_z: 3.52 },
            classified_by: 'alice',
          },
        ],
      },
    })!;
    const [item] = record.drift!.drifted;
    expect(describeSignedDrift(item)).toBe(
      'consequence stop_gained → stop_gained&splice_region_variant; LoF LC → HC; gnomAD missense Z 2.95 → 3.52',
    );
  });

  it('says why an SV/CNV classification counted as drift when the version was signed', () => {
    const evidence = { chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4', 'TXNL1'], pli: 0.99, sv_type: 'DEL', source: 'needlr', inheritance: 'de_novo' };
    const item = { variantId: 'sv', changed: [], evidenceFrom: evidence, evidenceTo: evidence, classifiedBy: null };
    expect(describeSignedStructuralDrift({ ...item, status: 'variant_missing' })).toBe('no longer present in the data');
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
      describeBreachedMetric({ label: 'Mean coverage', value: 18, unit: '×', verdict: 'warn', warnValue: 20, errorValue: 10 }),
    ).toBe('Mean coverage 18 × (Warning; warning 20, fail 10)');
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
