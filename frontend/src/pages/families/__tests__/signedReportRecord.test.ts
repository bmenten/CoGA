// REQ-TRACE-007: a signed version is rendered from its frozen snapshot alone. These pin how
// the snapshot is read: every section the record holds, a section an older record predates
// as absent (never as empty), and an entry that cannot be read kept, not dropped.
import { describe, expect, it } from 'vitest';

import {
  ANALYTICAL_SIGN_OFF_STATEMENT,
  describeBreachedMetric,
  describeCatalogue,
  describeCnvKnowledgebase,
  describeFrozenEvidenceValues,
  describeGenePanel,
  describeGeneReferenceReleases,
  describeGenotype,
  describeModuleVersions,
  describeNiptCoverage,
  describeNiptFetalFraction,
  describeNiptFetalSex,
  describeNiptFunnel,
  describeNiptVariant,
  describeRepeatCall,
  describeRepeatThresholds,
  describeReportSections,
  describeSignedDrift,
  describeSignedStructuralDrift,
  describeSmallVariantDescription,
  describeSmallVariantEvidenceChange,
  describeSmallVariantLocus,
  describeStructuralEvidenceChange,
  describeStructuralVariantDescription,
  isNiptRecord,
  liveReportSignedNotice,
  liveReportSignedState,
  NIPT_NOT_CLASSIFIED_REASONS,
  paraphaseStatusWords,
  parseSignedReport,
  parseSignedVersionParam,
  reportViewSearch,
} from '../signedReportRecord';
import { SIGNED_ANALYSIS } from '../../../test/signedAnalysisFixture';

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
    // Frozen before CoGA froze the frequencies and in-silico scores: the record holds none.
    expect(classified.evidence).toEqual({
      clinvar: 'Likely_pathogenic',
      annotationVersion: 'v1',
      capturedAt: '2026-06-20T08:30:00+00:00',
      transcriptId: null,
      frequencies: null,
      inSilico: null,
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
