// Auto-evaluator: maps the data already on a variant (consequence, ClinVar,
// gnomAD frequency, in-silico scores) plus optional gene/phenotype context to
// suggested ACMG criteria. Pure and side-effect free.
//
// Every result is a *suggestion* only — the UI requires the analyst to accept it
// before it contributes points. Criteria needing human judgement that we cannot
// derive from data (PS1, PS3, PM5, segregation, de novo, …) are never emitted.

import { ACMG_CRITERIA_BY_CODE } from './criteria';
import type {
  AcmgCriterionCode,
  AcmgDisposition,
  AcmgFamilyContext,
  AcmgGeneContext,
  AcmgPhenotypeContext,
  AcmgStrength,
  AcmgSuggestion,
} from './types';

// Subset of SmallVariant fields the evaluator reads. SmallVariant satisfies this
// structurally, so callers pass the variant directly.
export interface AcmgVariantInput {
  effect?: string;
  // Gene/locus symbol (used by the mt evaluator for evidence strings).
  gene?: string;
  impact?: string;
  clinvar?: string;
  lof?: string;
  gnomad_af?: number;
  gnomad_hom_count?: number;
  gene_missense_z?: number;
  revel?: number;
  spliceai_max?: number;
  annotation_extra?: Record<string, string | number | boolean | null> | null;
}

// ---- Thresholds (named so they are easy to audit/tune) ----

// gnomAD allele-frequency cut-offs.
const BA1_AF = 0.05; // stand-alone benign
const BS1_AF = 0.01; // greater than expected for most Mendelian disorders
const PM2_AF = 1e-4; // rare enough to support pathogenicity

// REVEL → strength (ClinGen Sequence Variant Interpretation calibration, 2022).
const REVEL_PP3_STRONG = 0.932;
const REVEL_PP3_MODERATE = 0.773;
const REVEL_PP3_SUPPORTING = 0.644;
const REVEL_BP4_STRONG = 0.016;
const REVEL_BP4_MODERATE = 0.183;
const REVEL_BP4_SUPPORTING = 0.29;

// SpliceAI max delta score cut-offs.
const SPLICEAI_PP3_MODERATE = 0.5;
const SPLICEAI_PP3_SUPPORTING = 0.2;
const SPLICEAI_NO_IMPACT = 0.1; // below this, no predicted splice effect

// gnomAD missense-Z constraint for PP2.
const PP2_MISSENSE_Z = 3.09;

// PP4 phenotype-specificity (Monarch Resnik best-match-average score in [0, 1]).
// Per ClinGen PP1/PP4 guidance, a more specific gene↔phenotype match warrants a
// stronger PP4. Auto caps at Moderate; the analyst may still raise it to Strong.
const PP4_MODERATE_SCORE = 0.6; // highly specific match → PP4_Moderate
const PP4_SUPPORTING_SCORE = 0.3; // meaningful match → PP4_Supporting

export const LOF_EFFECTS = [
  'stop_gained',
  'frameshift_variant',
  'splice_acceptor_variant',
  'splice_donor_variant',
  'start_lost',
  'transcript_ablation',
];

const PROTEIN_LENGTH_EFFECTS = [
  'inframe_insertion',
  'inframe_deletion',
  'stop_lost',
  'protein_altering_variant',
];

export function effectIncludes(effect: string | undefined, needles: string[]): boolean {
  if (!effect) return false;
  const lower = effect.toLowerCase();
  return needles.some((needle) => lower.includes(needle));
}

function isMissense(effect: string | undefined): boolean {
  return effectIncludes(effect, ['missense_variant']);
}

function isSynonymous(effect: string | undefined): boolean {
  return effectIncludes(effect, ['synonymous_variant']);
}

function clinvarSays(clinvar: string | undefined, needles: string[]): boolean {
  if (!clinvar) return false;
  const lower = clinvar.toLowerCase();
  // Avoid matching "non-pathogenic"/"likely_benign" when looking for pathogenic.
  return needles.some((needle) => lower.includes(needle));
}

function isRecessiveGene(gene?: AcmgGeneContext): boolean {
  return (gene?.modesOfInheritance ?? []).some((moi) =>
    moi.toLowerCase().includes('recessive'),
  );
}

export function fmtAf(af: number): string {
  return af < 1e-4 ? af.toExponential(1) : `${(af * 100).toPrecision(3)}%`;
}

// ---- Molecular consequence class → criteria that cannot apply ----

type MolecularClass = 'lof' | 'missense' | 'synonymous' | 'inframe' | 'splice_region' | 'other';

function molecularClass(effect: string | undefined): MolecularClass {
  if (effectIncludes(effect, LOF_EFFECTS)) return 'lof';
  if (isMissense(effect)) return 'missense';
  if (isSynonymous(effect)) return 'synonymous';
  if (effectIncludes(effect, PROTEIN_LENGTH_EFFECTS)) return 'inframe';
  if (effectIncludes(effect, ['splice_region_variant', 'splice_polypyrimidine', 'intron_variant'])) {
    return 'splice_region';
  }
  return 'other';
}

// Criteria ruled out per molecular class, with a short reason for the tooltip.
const NOT_APPLICABLE: Record<MolecularClass, { codes: AcmgCriterionCode[]; reason: string }> = {
  lof: {
    codes: ['PP2', 'PM5', 'BP1', 'BP7', 'BP3'],
    reason: 'Not applicable to a loss-of-function variant.',
  },
  missense: {
    codes: ['PVS1', 'PM4', 'BP3', 'BP7'],
    reason: 'Not applicable to a missense variant.',
  },
  synonymous: {
    codes: ['PVS1', 'PM4', 'PP2', 'PM5', 'BP1', 'BP3'],
    reason: 'Not applicable to a synonymous variant.',
  },
  inframe: {
    codes: ['PVS1', 'PP2', 'PM5', 'BP1', 'BP7'],
    reason: 'Not applicable to an in-frame/length-changing variant.',
  },
  splice_region: {
    codes: ['PVS1', 'PP2', 'PM5', 'BP1', 'PM4'],
    reason: 'Not applicable to a splice-region variant.',
  },
  other: { codes: [], reason: '' },
};

// ---- Trio / segregation genotype helpers ----

// Number of alternate alleles in a VCF genotype, or null if missing/unparseable.
export function altAlleleCount(gt?: string): number | null {
  if (!gt) return null;
  const normalized = gt.replace(/\|/g, '/').trim();
  if (!normalized || normalized.includes('.')) return null;
  const alleles = normalized.split('/').map((a) => Number.parseInt(a, 10));
  if (alleles.some((a) => Number.isNaN(a))) return null;
  return alleles.filter((a) => a > 0).length;
}

// ---- Evaluator ----

export function evaluateAcmg(
  variant: AcmgVariantInput,
  gene?: AcmgGeneContext,
  phenotype?: AcmgPhenotypeContext,
  family?: AcmgFamilyContext,
): AcmgSuggestion[] {
  const out: AcmgSuggestion[] = [];
  const add = (
    code: AcmgCriterionCode,
    strength: AcmgStrength,
    evidence: string,
    disposition: AcmgDisposition = 'applies',
  ) => out.push({ code, strength, evidence, disposition });
  const against = (code: AcmgCriterionCode, evidence: string) =>
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', evidence, 'contraindicated');
  const notApplicable = (code: AcmgCriterionCode, evidence: string) =>
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', evidence, 'not_applicable');

  const af = variant.gnomad_af;
  const effect = variant.effect;

  // ---- PVS1: predicted null in a LOF-mechanism gene ----
  if (effectIncludes(effect, LOF_EFFECTS)) {
    const hc = variant.lof === 'HC';
    const haplo = (gene?.clingenDosageHaploinsufficiency ?? '').toLowerCase();
    const lofMechanism = haplo.includes('sufficient evidence');
    if (gene?.unavailable) {
      // Not "unconfirmed": whether the gene has a LOF mechanism was never looked up (#609).
      add(
        'PVS1',
        'strong',
        `${effect} predicted null${hc ? ' (LOFTEE high-confidence)' : ''}; the gene profile could not be loaded, so its LOF mechanism (ClinGen haploinsufficiency) is not assessed — review before applying.`,
        'consider',
      );
    } else if (lofMechanism) {
      add(
        'PVS1',
        hc ? 'very_strong' : 'strong',
        `${effect} predicted null${hc ? ' (LOFTEE high-confidence)' : ''}; ClinGen haploinsufficiency: ${gene?.clingenDosageHaploinsufficiency}.`,
      );
    } else {
      // Null variant, but LOF mechanism unconfirmed — surface it without auto-applying.
      add(
        'PVS1',
        'strong',
        `${effect} predicted null${hc ? ' (LOFTEE high-confidence)' : ''}, but LOF disease mechanism is unconfirmed — review gene mechanism before applying.`,
        'consider',
      );
    }
  }

  // ---- PM4: protein length change ----
  if (effectIncludes(effect, PROTEIN_LENGTH_EFFECTS)) {
    add(
      'PM4',
      'moderate',
      `${effect} changes protein length (confirm it is outside a repeat region).`,
      'consider',
    );
  }

  // ---- Frequency: BA1 / BS1 / PM2 — only the matching band applies, the rest
  //      are ruled out by the observed allele frequency (marked not applicable). ----
  if (af != null) {
    if (af >= BA1_AF) {
      add('BA1', 'stand_alone', `gnomAD allele frequency ${fmtAf(af)} ≥ 5% — stand-alone benign.`);
      notApplicable('PM2', `Not rare: gnomAD allele frequency ${fmtAf(af)}.`);
      notApplicable('BS1', `Subsumed by BA1 at gnomAD allele frequency ${fmtAf(af)}.`);
    } else if (af >= BS1_AF) {
      add('BS1', 'strong', `gnomAD allele frequency ${fmtAf(af)} is higher than expected for a Mendelian disorder.`);
      notApplicable('BA1', `gnomAD allele frequency ${fmtAf(af)} is below 5%.`);
      notApplicable('PM2', `Not rare: gnomAD allele frequency ${fmtAf(af)}.`);
    } else if (af < PM2_AF) {
      add('PM2', 'supporting', `gnomAD allele frequency ${fmtAf(af)} is very rare.`);
      notApplicable('BA1', `Rare: gnomAD allele frequency ${fmtAf(af)} (< 5%).`);
      notApplicable('BS1', `Rare: gnomAD allele frequency ${fmtAf(af)}.`);
    } else {
      // Borderline band — none of the frequency criteria apply.
      notApplicable('PM2', `gnomAD allele frequency ${fmtAf(af)} is not rare enough for PM2.`);
      notApplicable('BA1', `gnomAD allele frequency ${fmtAf(af)} is below 5%.`);
      notApplicable('BS1', `gnomAD allele frequency ${fmtAf(af)} is below the BS1 threshold.`);
    }
  } else {
    add('PM2', 'supporting', 'Absent from gnomAD.');
    notApplicable('BA1', 'Absent from gnomAD — not a common variant.');
    notApplicable('BS1', 'Absent from gnomAD — not a common variant.');
  }

  // ---- BS2: observed homozygous in population; ruled out when none observed. ----
  if ((variant.gnomad_hom_count ?? 0) > 0) {
    const recessive = isRecessiveGene(gene);
    add(
      'BS2',
      recessive ? 'strong' : 'supporting',
      `${variant.gnomad_hom_count} homozygote(s) in gnomAD${
        recessive
          ? ' for a recessive-associated gene'
          : gene?.unavailable
            ? ' (the gene profile could not be loaded: confirm inheritance mode)'
            : ' (confirm inheritance mode)'
      }.`,
      recessive ? 'applies' : 'consider',
    );
  } else {
    notApplicable('BS2', 'No homozygotes observed in gnomAD.');
  }

  // ---- PP2: missense in a constrained gene ----
  if (isMissense(effect) && (variant.gene_missense_z ?? 0) >= PP2_MISSENSE_Z) {
    add('PP2', 'supporting', `Missense in a missense-constrained gene (gnomAD missense Z = ${variant.gene_missense_z}).`);
  }

  // ---- PP3 / BP4: in-silico evidence (REVEL preferred, then AlphaMissense) ----
  inSilicoSuggestions(variant).forEach((suggestion) => out.push(suggestion));

  // ---- BP7: synonymous with no predicted splice impact ----
  if (isSynonymous(effect)) {
    if (variant.spliceai_max == null || variant.spliceai_max < SPLICEAI_NO_IMPACT) {
      add('BP7', 'supporting', 'Synonymous variant with no predicted SpliceAI splice impact.');
    } else {
      against('BP7', `Synonymous, but SpliceAI ${variant.spliceai_max.toFixed(2)} predicts a possible splice effect.`);
    }
  }

  // ---- PP5 / BP6: ClinVar reputable-source assertion ----
  if (clinvarSays(variant.clinvar, ['pathogenic'])) {
    add('PP5', 'supporting', `ClinVar reports ${variant.clinvar}.`);
    against('BP6', `ClinVar reports ${variant.clinvar}, not benign.`);
  } else if (clinvarSays(variant.clinvar, ['benign'])) {
    add('BP6', 'supporting', `ClinVar reports ${variant.clinvar}.`);
    against('PP5', `ClinVar reports ${variant.clinvar}, not pathogenic.`);
  }

  // ---- PP4: proband phenotype specific for the gene ----
  // Prefer the Monarch phenotype-specificity score (scales strength); fall back to
  // a direct HPO-ID overlap when no score is available (no prioritization run).
  pp4Suggestion(gene, phenotype).forEach((suggestion) => out.push(suggestion));

  // ---- Trio / segregation: de novo (PS2/PM6), cosegregation (PP1/BS4) ----
  evaluateFamily(family, add, notApplicable);

  // ---- Exclude criteria the molecular consequence rules out ----
  const klass = molecularClass(effect);
  const { codes, reason } = NOT_APPLICABLE[klass];
  for (const code of codes) {
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', reason, 'not_applicable');
  }

  return out;
}

type AddFn = (
  code: AcmgCriterionCode,
  strength: AcmgStrength,
  evidence: string,
  disposition?: AcmgDisposition,
) => void;

function evaluateFamily(
  family: AcmgFamilyContext | undefined,
  add: AddFn,
  notApplicable: (code: AcmgCriterionCode, evidence: string) => void,
): void {
  const members = family?.members ?? [];
  const byRole = (role: string) =>
    members.find((m) => (m.role ?? '').toLowerCase() === role);
  const proband = byRole('proband') ?? members.find((m) => m.affected);
  const probandCarries = proband ? (altAlleleCount(proband.gt) ?? 0) > 0 : false;

  const father = byRole('father');
  const mother = byRole('mother');
  const fa = father ? altAlleleCount(father.gt) : null;
  const ma = mother ? altAlleleCount(mother.gt) : null;

  // ---- De novo (PS2 / PM6) ----
  if (probandCarries && fa != null && ma != null) {
    if (fa === 0 && ma === 0) {
      add(
        'PM6',
        'moderate',
        'Absent in both sequenced parents (de novo) — parentage not molecularly confirmed (use PS2 if confirmed).',
        'applies',
      );
    } else {
      // Inherited — de novo criteria are ruled out.
      notApplicable('PS2', 'Inherited from a parent — not de novo.');
      notApplicable('PM6', 'Inherited from a parent — not de novo.');
    }
  } else {
    // No complete trio — de novo cannot be assessed.
    notApplicable('PS2', 'No complete parental genotypes — de novo cannot be assessed.');
    notApplicable('PM6', 'No complete parental genotypes — de novo cannot be assessed.');
  }

  // ---- Segregation (PP1 / BS4) ----
  const affectedCarriers = members.filter((m) => m.affected && (altAlleleCount(m.gt) ?? 0) > 0);
  const affectedNonCarriers = members.filter((m) => m.affected && altAlleleCount(m.gt) === 0);
  if (affectedCarriers.length >= 2) {
    add(
      'PP1',
      'supporting',
      `Variant carried by ${affectedCarriers.length} affected family members (cosegregation).`,
      'consider',
    );
  } else {
    notApplicable('PP1', 'No additional affected carriers — cosegregation cannot be shown.');
  }
  if (affectedNonCarriers.length >= 1) {
    add(
      'BS4',
      'strong',
      `${affectedNonCarriers.length} affected family member(s) do not carry the variant (lack of segregation).`,
      'consider',
    );
  } else {
    notApplicable('BS4', 'No affected relative lacking the variant — lack of segregation cannot be shown.');
  }
}

/**
 * PP4 when the lookup it needs failed: surfaced for review, never auto-applied, and never
 * read as "no phenotype match" (#609).
 */
export function pp4NotAssessed(
  gene: AcmgGeneContext | undefined,
  phenotype: AcmgPhenotypeContext | undefined,
): AcmgSuggestion | null {
  const missing = [
    gene?.unavailable ? "the gene's HPO associations" : null,
    phenotype?.probandHpoUnavailable ? "the family's HPO terms" : null,
  ].filter(Boolean);
  if (!missing.length) return null;
  return {
    code: 'PP4',
    strength: 'supporting',
    evidence: `Not assessed: ${missing.join(' and ')} could not be loaded. Review the phenotype match by hand.`,
    disposition: 'consider',
  };
}

// PP4: phenotype specific for the gene. Scales strength from the Monarch
// gene↔proband phenotype score when present, otherwise falls back to a binary
// HPO-ID overlap at Supporting. Returns at most one suggestion.
function pp4Suggestion(
  gene: AcmgGeneContext | undefined,
  phenotype: AcmgPhenotypeContext | undefined,
): AcmgSuggestion[] {
  const probandHpo = phenotype?.probandHpoIds ?? [];
  const geneHpo = new Set(gene?.geneHpoIds ?? []);
  const overlap = probandHpo.filter((id) => geneHpo.has(id));
  const score = phenotype?.phenotypeScore;
  // Without a usable score the overlap is all there is, and it needs both lookups.
  const notAssessed = score != null && score >= PP4_SUPPORTING_SCORE ? null : pp4NotAssessed(gene, phenotype);
  if (notAssessed) return [notAssessed];

  const matchedLabels = (phenotype?.phenotypeMatches ?? [])
    .map((m) => m.label || m.hpo_id)
    .filter(Boolean)
    .slice(0, 3);

  if (score != null && score >= PP4_SUPPORTING_SCORE) {
    const strength: AcmgStrength = score >= PP4_MODERATE_SCORE ? 'moderate' : 'supporting';
    const detail = matchedLabels.length ? ` Top matches: ${matchedLabels.join(', ')}.` : '';
    return [
      {
        code: 'PP4',
        strength,
        evidence: `Gene–phenotype specificity score ${score.toFixed(2)} (Monarch) supports a gene-specific phenotype.${detail}`,
        disposition: 'applies',
      },
    ];
  }

  // No usable score → fall back to direct HPO-ID overlap (Supporting).
  if (overlap.length) {
    return [
      {
        code: 'PP4',
        strength: 'supporting',
        evidence: `Proband HPO term(s) overlap the gene's phenotype (${overlap.length} shared).`,
        disposition: 'applies',
      },
    ];
  }
  return [];
}

// In-silico evidence: a positive PP3/BP4 suggestion plus an explicit
// contraindication of the opposite criterion when the signal is clear.
function inSilicoSuggestions(variant: AcmgVariantInput): AcmgSuggestion[] {
  const { revel } = variant;
  const splice = variant.spliceai_max ?? 0;

  let spliceStrength: AcmgStrength | null = null;
  if (splice >= SPLICEAI_PP3_MODERATE) spliceStrength = 'moderate';
  else if (splice >= SPLICEAI_PP3_SUPPORTING) spliceStrength = 'supporting';

  const contra = (code: AcmgCriterionCode, evidence: string): AcmgSuggestion => ({
    code,
    strength: ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting',
    evidence,
    disposition: 'contraindicated',
  });

  if (revel != null) {
    if (revel >= REVEL_PP3_SUPPORTING) {
      const strength: AcmgStrength =
        revel >= REVEL_PP3_STRONG ? 'strong' : revel >= REVEL_PP3_MODERATE ? 'moderate' : 'supporting';
      return [
        {
          code: 'PP3',
          strength: strongerOf(strength, spliceStrength),
          evidence: `REVEL ${revel.toFixed(3)} supports a deleterious effect${spliceStrength ? `; SpliceAI ${splice.toFixed(2)}` : ''}.`,
          disposition: 'applies',
        },
        contra('BP4', `REVEL ${revel.toFixed(3)} is deleterious, not benign.`),
      ];
    }
    if (revel <= REVEL_BP4_SUPPORTING && splice < SPLICEAI_NO_IMPACT) {
      const strength: AcmgStrength =
        revel <= REVEL_BP4_STRONG ? 'strong' : revel <= REVEL_BP4_MODERATE ? 'moderate' : 'supporting';
      return [
        {
          code: 'BP4',
          strength,
          evidence: `REVEL ${revel.toFixed(3)} and SpliceAI ${splice.toFixed(2)} support no functional impact.`,
          disposition: 'applies',
        },
        contra('PP3', `REVEL ${revel.toFixed(3)} is benign-leaning, not deleterious.`),
      ];
    }
  }

  if (spliceStrength) {
    return [
      {
        code: 'PP3',
        strength: spliceStrength,
        evidence: `SpliceAI max delta ${splice.toFixed(2)} predicts a splice effect.`,
        disposition: 'applies',
      },
    ];
  }

  const amClass = String(variant.annotation_extra?.alphamissense_class ?? '').toLowerCase();
  if (amClass.includes('pathogenic')) {
    return [{ code: 'PP3', strength: 'supporting', evidence: 'AlphaMissense class: likely pathogenic.', disposition: 'applies' }];
  }
  if (amClass.includes('benign')) {
    return [{ code: 'BP4', strength: 'supporting', evidence: 'AlphaMissense class: likely benign.', disposition: 'applies' }];
  }

  // No usable in-silico predictions at all → the computational criteria are ruled out.
  const hasAnyPrediction =
    revel != null || variant.spliceai_max != null || amClass.includes('pathogenic') || amClass.includes('benign');
  if (!hasAnyPrediction) {
    const na = (code: AcmgCriterionCode): AcmgSuggestion => ({
      code,
      strength: 'supporting',
      evidence: 'No in-silico predictions available.',
      disposition: 'not_applicable',
    });
    return [na('PP3'), na('BP4')];
  }

  // Predictions exist but are inconclusive (REVEL in the grey zone) → leave neutral.
  return [];
}

const STRENGTH_ORDER: AcmgStrength[] = ['supporting', 'moderate', 'strong', 'very_strong'];

function strongerOf(a: AcmgStrength, b: AcmgStrength | null): AcmgStrength {
  if (!b) return a;
  return STRENGTH_ORDER.indexOf(a) >= STRENGTH_ORDER.indexOf(b) ? a : b;
}
