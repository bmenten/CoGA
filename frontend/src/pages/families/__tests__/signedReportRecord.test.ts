// REQ-TRACE-007: a signed version is rendered from its frozen snapshot alone. These pin how
// the snapshot is read: every section the record holds, a section an older record predates
// as absent (never as empty), and an entry that cannot be read kept, not dropped.
import { describe, expect, it } from 'vitest';

import {
  describeBreachedMetric,
  describeModuleVersions,
  describeSignedDrift,
  describeSignedStructuralDrift,
  describeStructuralEvidenceChange,
  parseSignedReport,
  parseSignedVersionParam,
  reportViewSearch,
} from '../signedReportRecord';

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
      drifted: [{ variantId: '1-100-A-G', status: 'no_snapshot', clinvarFrom: null, clinvarTo: null, classifiedBy: null }],
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
    expect(classified.evidence).toEqual({
      clinvar: 'Likely_pathogenic',
      annotationVersion: 'v1',
      capturedAt: '2026-06-20T08:30:00+00:00',
    });

    // No ACMG criteria: the class its classification tag names; no evidence was frozen.
    expect(tagged.classification).toBe('VUS - class 3');
    expect(tagged.pointTotal).toBeNull();
    expect(tagged.criteria).toEqual([]);
    expect(tagged.evidence).toBeNull();

    expect(unreadable.variantId).toBeNull();
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
    });
    expect(record.importAcknowledgement).toEqual({ acknowledged: true, reason: 'SVs not requested.' });
    expect(record.sequencingQc?.unavailable).toBe('QC thresholds could not be resolved');
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
    const item = { variantId: 'v', clinvarFrom: null, clinvarTo: null, classifiedBy: null };
    expect(describeSignedDrift({ ...item, status: 'no_snapshot' })).toBe(
      'no frozen evidence (not saved through ACMG classify)',
    );
    expect(describeSignedDrift({ ...item, status: 'variant_missing' })).toBe('no longer present in the data');
    expect(describeSignedDrift({ ...item, status: 'drifted', clinvarFrom: 'VUS', clinvarTo: 'Pathogenic' })).toBe(
      'ClinVar VUS → Pathogenic',
    );
    expect(describeSignedDrift({ ...item, status: 'drifted' })).toBe('annotation set changed');
  });

  it('says why an SV/CNV classification counted as drift when the version was signed', () => {
    const evidence = { chrom: '18', start: 55000000, end: 55400000, gene_symbols: ['TCF4', 'TXNL1'], pli: 0.99, sv_type: 'DEL', source: 'needlr', inheritance: 'de_novo' };
    const item = { variantId: 'sv', changed: [], evidenceFrom: evidence, evidenceTo: evidence, classifiedBy: null };
    expect(describeSignedStructuralDrift({ ...item, status: 'variant_missing' })).toBe('no longer present in the data');
    expect(describeSignedStructuralDrift({ ...item, status: 'no_snapshot' })).toBe(
      'no frozen evidence (no CNV scoring saved, or saved before CoGA froze its evidence)',
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
