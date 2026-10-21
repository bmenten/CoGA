/**
 * The SV type without its subtypes, in capitals: DEL:ME is DEL, as the server reads it
 * (`sv_kind`). One copy for the SV tracks (`svTypes.ts`) and the CNV classifier
 * (`cnvAcmg/evaluate.ts`), which kept one of its own (ENG-7).
 */
export const svKind = (type?: string | null): string => (type ?? '').trim().toUpperCase().split(':')[0];
