// The frontend's hand-written API types, checked against the backend's schema (#528).
//
// apiSchema.generated.ts is generated from the backend's OpenAPI schema, and CI fails when
// it is out of date (scripts/generate-api-types.py --check). Each check below fails to
// type-check when the response the backend serves no longer fits the hand-written type
// that reads it: a field removed or renamed, made nullable, or given another type. A
// hand-written type may narrow a backend string to the values it knows (a QC status, a
// clinical status); Widen lets that narrowing through.
//
// New code can read a response through its generated type directly.
import type * as Schema from './apiSchema.generated';
import type * as Api from './apiTypes';

type Widen<T> = T extends string
  ? string
  : T extends number
    ? number
    : T extends boolean
      ? boolean
      : T extends (infer U)[]
        ? Widen<U>[]
        : T extends object
          ? { [K in keyof T]: Widen<T[K]> }
          : T;

/** True when every response of the backend's shape is something the frontend type reads. */
type Fits<Frontend, Backend> = [Backend] extends [Widen<Frontend>] ? true : false;
type Check<T extends true> = T;

export type ApiContract = [
  Check<Fits<Api.ApiFamilyMember, Schema.FamilyMemberOut>>,
  Check<Fits<Api.ApiSampleSequencingQcMetric, Schema.SampleSequencingQcMetricOut>>,
  Check<Fits<Api.ApiSampleSequencingQcEvaluation, Schema.SampleSequencingQcEvaluationOut>>,
  Check<Fits<Api.ApiQcThreshold, Schema.QcThresholdOut>>,
  Check<Fits<Api.ApiQcThresholdProfile, Schema.QcThresholdProfileOut>>,
  Check<Fits<Api.ApiQcThresholdChange, Schema.QcThresholdChangeOut>>,
  Check<Fits<Api.ApiQcThresholdCatalogue, Schema.QcThresholdCatalogueOut>>,
  Check<Fits<Api.ApiFamilyMemberImpact, Schema.FamilyMemberImpactOut>>,
  Check<Fits<Api.ApiFamilyMemberDetail, Schema.FamilyMemberDetailOut>>,
  Check<Fits<Api.ApiFamilyMemberBatchUpdateItem, Schema.FamilyMemberBatchUpdateItem>>,
  Check<Fits<Api.ApiFamilyRelationship, Schema.FamilyRelationshipOut>>,
  Check<Fits<Api.ApiFamilyStructureVersion, Schema.FamilyStructureVersionOut>>,
  Check<Fits<Api.ApiFamilyRegionOfInterest, Schema.FamilyRegionOfInterestOut>>,
  Check<Fits<Api.ApiUserRef, Schema.UserRefOut>>,
  Check<Fits<Api.ApiFamilyStatusRef, Schema.FamilyStatusRef>>,
  Check<Fits<Api.ApiHpoTerm, Schema.HpoTermOut>>,
  Check<Fits<Api.ApiHpoAnnotation, Schema.HpoAnnotationOut>>,
  Check<Fits<Api.ApiHpoFamilyQuery, Schema.HpoFamilyQueryOut>>,
  Check<Fits<Api.ApiSmallVariantReviewSummary, Schema.SmallVariantReviewSummaryOut>>,
  Check<Fits<Api.ApiNiptFetalFraction, Schema.NiptFetalFractionOut>>,
  Check<Fits<Api.ApiNiptSummary, Schema.NiptSummaryOut>>,
  Check<Fits<Api.ApiNiptCoverageRegion, Schema.NiptCoverageRegionOut>>,
  Check<Fits<Api.ApiNiptCoverageLowRegion, Schema.NiptCoverageLowRegionOut>>,
  Check<Fits<Api.ApiNiptCoverageSummary, Schema.NiptCoverageSummaryOut>>,
  Check<Fits<Api.ApiSampleIntegritySexCheck, Schema.SampleIntegritySexCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityRelatednessCheck, Schema.SampleIntegrityRelatednessCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityMendelianCheck, Schema.SampleIntegrityMendelianCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityPaternityCheck, Schema.SampleIntegrityPaternityCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityFetalSexCheck, Schema.SampleIntegrityFetalSexCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityCategoryQc, Schema.SampleIntegrityCategoryQcOut>>,
  Check<Fits<Api.ApiAnnotationModule, Schema.AnnotationModuleOut>>,
  Check<Fits<Api.ApiAnnotationManifest, Schema.AnnotationManifestOut>>,
  Check<Fits<Api.ApiClassificationDriftItem, Schema.ClassificationDriftItem>>,
  Check<Fits<Api.ApiClassificationDrift, Schema.ClassificationDriftOut>>,
  Check<Fits<Api.ApiStructuralClassificationDriftItem, Schema.StructuralClassificationDriftItem>>,
  Check<Fits<Api.ApiStructuralClassificationDrift, Schema.StructuralClassificationDriftOut>>,
  Check<Fits<Api.ApiClinicalAuditEvent, Schema.ClinicalAuditEventOut>>,
  Check<Fits<Api.ApiClinicalAudit, Schema.ClinicalAuditOut>>,
  Check<Fits<Api.ApiReportSignoutList, Schema.ReportSignoutListOut>>,
  Check<Fits<Api.ApiReportSnapshotGap, Schema.ReportSnapshotGapOut>>,
  Check<Fits<Api.ApiReportSignoutCheck, Schema.ReportSignoutCheckOut>>,
  Check<Fits<Api.ApiSampleIntegrityQc, Schema.SampleIntegrityQcOut>>,
  Check<Fits<Api.ApiClinicalCnv, Schema.ClinicalCnvOut>>,
  Check<Fits<Api.ApiChromosome, Schema.ChromosomeOut>>,
  Check<Fits<Api.ApiRepeatExpansionMotifCount, Schema.RepeatExpansionMotifCountOut>>,
  Check<Fits<Api.ApiRepeatExpansionAllele, Schema.RepeatExpansionAlleleOut>>,
  Check<Fits<Api.ApiRepeatExpansionSampleCall, Schema.RepeatExpansionSampleCallOut>>,
  Check<Fits<Api.ApiRepeatExpansionRow, Schema.RepeatExpansionRowOut>>,
  Check<Fits<Api.ApiFamilyRepeatExpansionTable, Schema.FamilyRepeatExpansionTableOut>>,
  Check<Fits<Api.ApiParaphaseMetric, Schema.ParaphaseMetricOut>>,
  Check<Fits<Api.ApiParaphaseHaplotypeGroup, Schema.ParaphaseHaplotypeGroupOut>>,
  Check<Fits<Api.ApiParaphaseDisorder, Schema.ParaphaseDisorderOut>>,
  Check<Fits<Api.ApiParaphaseClinical, Schema.ParaphaseClinicalOut>>,
  Check<Fits<Api.ApiParaphaseRegionInfo, Schema.ParaphaseRegionInfoOut>>,
  Check<Fits<Api.ApiParaphaseExtraField, Schema.ParaphaseExtraFieldOut>>,
  Check<Fits<Api.ApiParaphaseSampleResult, Schema.ParaphaseSampleResultOut>>,
  Check<Fits<Api.ApiParaphaseGeneResult, Schema.ParaphaseGeneResultOut>>,
  Check<Fits<Api.ApiFamilyParaphaseTable, Schema.FamilyParaphaseTableOut>>,
  Check<Fits<Api.ApiMitoDNACoverage, Schema.MitoDNACoverageOut>>,
  Check<Fits<Api.ApiMitoDNAQc, Schema.MitoDNAQcOut>>,
  Check<Fits<Api.ApiMitoDNASample, Schema.MitoDNASampleOut>>,
  Check<Fits<Api.ApiMitoDNAVariantSampleCall, Schema.MitoDNAVariantSampleCallOut>>,
  Check<Fits<Api.ApiMitoDNAVariantAnnotation, Schema.MitoDNAVariantAnnotationOut>>,
  Check<Fits<Api.ApiRepeatExpansionTrackItem, Schema.RepeatExpansionTrackItemOut>>,
  Check<Fits<Api.ApiRepeatExpansionTrackResponse, Schema.RepeatExpansionTrackResponse>>,
  Check<Fits<Api.ApiGithubRelease, Schema.GithubReleaseOut>>,
  Check<Fits<Api.ApiGithubReleaseCatalog, Schema.GithubReleaseCatalogOut>>,
  Check<Fits<Api.GeneLocation, Schema.GeneLocation>>,
  Check<Fits<Api.GenePanel, Schema.GenePanelOut>>,
  Check<Fits<Api.GenePanelVersionSummary, Schema.GenePanelVersionSummary>>,
  Check<Fits<Api.GenePanelVersionList, Schema.GenePanelVersionListOut>>,
  Check<Fits<Api.MendeliomeRegenerateResponse, Schema.MendeliomeRegenerateResponse>>,
]

// Not checked: ApiMitoDNAVariant (<- MitoDNAVariantOut) and ApiFamilyMitoDNAAnalysis
// (<- FamilyMitoDNAAnalysisOut). Their review's ACMG block, AcmgClassificationPayload, is
// also a request body, so its generated type keeps the request's optional fields, which a
// response always carries.
