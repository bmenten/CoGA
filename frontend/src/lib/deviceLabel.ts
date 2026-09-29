/**
 * The device label (TF-15 §1; IVDR Annex I §20): what CoGA is, its regulatory status and
 * its manufacturer, as the app footer and every report footer state them. The manufacturer
 * is the legal name and address of the declaration (TF-04; INPUTS-QUESTIONNAIRE A1).
 *
 * This wording is labelling: change it only under change control (TF-18), together with
 * TF-15.
 */
export const DEVICE_NAME = 'CoGA, Comprehensive Genomic Analysis';

export const DEVICE_STATUS =
  'In-house IVD per IVDR Article 5(5) · Not CE-marked · For internal CMGG use only';

export const DEVICE_MANUFACTURER =
  'Center for Medical Genetics, Ghent University Hospital, C. Heymanslaan 10, 9000 Ghent';
