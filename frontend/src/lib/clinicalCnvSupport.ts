import type { ApiClinicalCnv } from './apiTypes';

/**
 * The ClinVar support a knowledgebase build recorded for a clinical CNV (#624): how many
 * pathogenic ClinVar CNVs overlap it by at least 30 % reciprocally, per side. A knowledgebase
 * built without ClinVar records none, which is not the same as zero support.
 */
export const clinvarSupportRecorded = (cnv: ApiClinicalCnv): boolean =>
  cnv.clinvar_pathogenic_loss_count != null || cnv.clinvar_pathogenic_gain_count != null;

/** "12 loss · 3 gain", or null when the knowledgebase recorded no ClinVar support. */
export const clinvarSupportSummary = (cnv: ApiClinicalCnv): string | null =>
  clinvarSupportRecorded(cnv)
    ? `${cnv.clinvar_pathogenic_loss_count ?? 0} loss · ${cnv.clinvar_pathogenic_gain_count ?? 0} gain`
    : null;

export const CLINVAR_SUPPORT_NOT_RECORDED =
  'Not recorded: this knowledgebase was built without ClinVar support.';

/** The ClinVar page of a supporting record: a VariationID, or an RCV accession. */
export const clinvarRecordHref = (accession: string): string =>
  /^RCV\d+/i.test(accession)
    ? `https://www.ncbi.nlm.nih.gov/clinvar/${encodeURIComponent(accession)}/`
    : `https://www.ncbi.nlm.nih.gov/clinvar/variation/${encodeURIComponent(accession)}/`;
