// Auto-evaluator: maps the data already on a variant (consequence, ClinVar,
// gnomAD frequency, in-silico scores) plus optional gene, phenotype and family context
// (the trio's calls, the Sample QC's parentage checks) to suggested ACMG criteria. Pure and
// side-effect free.
//
// Every result is a *suggestion* only — the analyst can uncheck or change any of them
// before saving. Criteria needing human judgement that we cannot derive from data
// (PS1, PS3, PM5, …) are never emitted.

import type { VariantOut } from '../apiSchema.generated';
import { NUCLEAR_CHROMOSOMES, normalizeChrom } from '../chromosomes';
import { clinvarClass } from '../clinvar';
import { classifyGenotype } from '../genotypes';
import { ACMG_CRITERIA_BY_CODE, STRENGTH_LABELS } from './criteria';
import { parentsOf } from './pedigree';
import type {
  AcmgCriterionCode,
  AcmgDeNovoPhenotypeTier,
  AcmgDisposition,
  AcmgFamilyContext,
  AcmgFamilyMemberCall,
  AcmgGeneContext,
  AcmgParentageCheck,
  AcmgPhenotypeContext,
  AcmgStrength,
  AcmgSuggestion,
} from './types';

// Subset of SmallVariant fields the evaluator reads, as the variant lists serve them
// (VariantOut): a field without a value is null. SmallVariant satisfies this structurally, so
// callers pass the variant directly. `gene` is the gene/locus symbol (used by the mt evaluator
// for evidence strings); `alpha_missense_class` the AlphaMissense class as the variant is served
// with it (likely_benign, ambiguous, likely_pathogenic): the value an ACMG classification's
// evidence snapshot freezes; `chr` and `hemizygous_in_males` the locus, for the sex-aware de
// novo rule; `gene_missense_z` the gene profile's gnomAD missense Z and
// `annotation_extra` the VCF's own MISSENSE_Z (its `gene_missense_z`), which PP2 reads;
// `gnomad_annotated` the gnomAD datasets the variant's callset was annotated with, which PM2
// reads for a variant without a gnomAD frequency.
export type AcmgVariantInput = Partial<
  Pick<
    VariantOut,
    | 'effect'
    | 'gene'
    | 'impact'
    | 'clinvar'
    | 'clinvar_review_status'
    | 'clinvar_stars'
    | 'clinvar_conditions'
    | 'lof'
    | 'lof_flags'
    | 'nmd'
    | 'population_frequencies'
    | 'gnomad_af'
    | 'gnomad_annotated'
    | 'gnomad_hom_count'
    | 'gene_missense_z'
    | 'annotation_extra'
    | 'revel'
    | 'spliceai_max'
    | 'alpha_missense_class'
    | 'chr'
    | 'hemizygous_in_males'
  >
>;

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

// SpliceAI max delta score cut-offs. The variant cards colour SpliceAI by the same ones.
export const SPLICEAI_PP3_MODERATE = 0.5;
export const SPLICEAI_PP3_SUPPORTING = 0.2;
export const SPLICEAI_NO_IMPACT = 0.1; // below this, no predicted splice effect

// gnomAD missense-Z constraint for PP2: a Z of 3.09 or more stands in for a gene with few benign
// missense variants, the threshold ClinGen's Sequence Variant Interpretation working group
// discusses for PP2.
const PP2_MISSENSE_Z = 3.09;

// PP4 phenotype-specificity (Monarch Resnik best-match-average score in [0, 1]).
// Per ClinGen PP1/PP4 guidance, a more specific gene↔phenotype match warrants a
// stronger PP4. Auto caps at Moderate; the analyst may still raise it to Strong.
const PP4_MODERATE_SCORE = 0.6; // highly specific match → PP4_Moderate
const PP4_SUPPORTING_SCORE = 0.3; // meaningful match → PP4_Supporting

// PM6 (de novo): a parent's reference call is trusted from this read depth, as in the
// backend's de novo mode (clickhouse_variant_queries._DE_NOVO_MIN_PARENT_DP). Below it the
// parent may be a missed heterozygote and an inherited variant look de novo, so PM6 is offered
// for review, not applied. A call without a reported depth is not held against it, as in the
// backend.
const DE_NOVO_MIN_PARENT_DP = 8;

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

export function effectIncludes(effect: string | null | undefined, needles: string[]): boolean {
  if (!effect) return false;
  const lower = effect.toLowerCase();
  return needles.some((needle) => lower.includes(needle));
}

function isMissense(effect: string | null | undefined): boolean {
  return effectIncludes(effect, ['missense_variant']);
}

function isSynonymous(effect: string | null | undefined): boolean {
  return effectIncludes(effect, ['synonymous_variant']);
}

function isRecessiveGene(gene?: AcmgGeneContext): boolean {
  return (gene?.modesOfInheritance ?? []).some((moi) =>
    moi.toLowerCase().includes('recessive'),
  );
}

export function fmtAf(af: number): string {
  return af < 1e-4 ? af.toExponential(1) : `${(af * 100).toPrecision(3)}%`;
}

// Where the gnomAD missense Z PP2 reads comes from: the gene profile (the value the variant card
// shows as Missense Z and the ranking reads), else the VCF's own MISSENSE_Z, which the variant list
// serves in annotation_extra under the annotation's gene_missense_z (the owner's decision of
// 2026-10-28). An ACMG save freezes the same value and source with the classification
// (backend small_variant_review_acmg.pp2_gene_missense_z).
export type MissenseZSource = 'gene_profile' | 'vcf';

const MISSENSE_Z_SOURCE_NOTE: Record<MissenseZSource, string> = {
  gene_profile: 'from the gene profile',
  vcf: "from the VCF's MISSENSE_Z: the gene profile holds none",
};

const finiteNumber = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

/** The gnomAD missense Z PP2 reads, and its source; null when neither holds a finite number. */
export function pp2MissenseZ(variant: AcmgVariantInput): { value: number; source: MissenseZSource } | null {
  const profile = finiteNumber(variant.gene_missense_z);
  if (profile !== null) return { value: profile, source: 'gene_profile' };
  const vcf = finiteNumber(variant.annotation_extra?.gene_missense_z);
  return vcf !== null ? { value: vcf, source: 'vcf' } : null;
}

// ---- Molecular consequence class → criteria that cannot apply ----

type MolecularClass = 'lof' | 'missense' | 'synonymous' | 'inframe' | 'splice_region' | 'other';

function molecularClass(effect: string | null | undefined): MolecularClass {
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
    const caution = pvs1Caution(variant);
    if (gene?.unavailable) {
      // Not "unconfirmed": whether the gene has a LOF mechanism was never looked up.
      add(
        'PVS1',
        'strong',
        `${effect} predicted null${hc ? ' (LOFTEE high-confidence)' : ''}; the gene profile could not be loaded, so its LOF mechanism (ClinGen haploinsufficiency) is not assessed — review before applying.`,
        'consider',
      );
    } else if (lofMechanism && caution) {
      // A LOFTEE flag or a predicted escape from nonsense-mediated decay: the null effect is
      // in doubt, so PVS1 is offered, not applied (the owner's choice of 2026-11-06).
      add(
        'PVS1',
        'strong',
        `${effect} predicted null${hc ? ' (LOFTEE high-confidence)' : ''}, but ${caution}; ClinGen haploinsufficiency: ${gene?.clingenDosageHaploinsufficiency} — review before applying.`,
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
  //      are ruled out by the observed frequency (marked not applicable). BA1 and BS1 read
  //      gnomAD's filtering allele frequency (FAF95, the highest over its genetic ancestry
  //      groups) where it is annotated, else the allele frequency; PM2 reads the allele
  //      frequency (the owner's choice of 2026-11-06). ----
  const faf = variant.population_frequencies?.gnomad_faf95_af;
  const benignAf = typeof faf === 'number' && Number.isFinite(faf) ? faf : af;
  const benignLabel =
    typeof faf === 'number' && Number.isFinite(faf) ? 'gnomAD filtering allele frequency (FAF95)' : 'gnomAD allele frequency';
  if (benignAf != null && benignAf >= BA1_AF) {
    add('BA1', 'stand_alone', `${benignLabel} ${fmtAf(benignAf)} ≥ 5% — stand-alone benign.`);
    notApplicable('PM2', `Not rare: ${benignLabel} ${fmtAf(benignAf)}.`);
    notApplicable('BS1', `Subsumed by BA1 at ${benignLabel} ${fmtAf(benignAf)}.`);
  } else if (benignAf != null && benignAf >= BS1_AF) {
    add('BS1', 'strong', `${benignLabel} ${fmtAf(benignAf)} is higher than expected for a Mendelian disorder.`);
    notApplicable('BA1', `${benignLabel} ${fmtAf(benignAf)} is below 5%.`);
    notApplicable('PM2', `Not rare: ${benignLabel} ${fmtAf(benignAf)}.`);
  } else if (af != null) {
    if (af < PM2_AF) {
      add('PM2', 'supporting', `gnomAD allele frequency ${fmtAf(af)} is very rare.`);
      notApplicable('BA1', `Rare: ${benignLabel} ${fmtAf(benignAf ?? af)} (< 5%).`);
      notApplicable('BS1', `Rare: ${benignLabel} ${fmtAf(benignAf ?? af)}.`);
    } else {
      // Borderline band — none of the frequency criteria apply.
      notApplicable('PM2', `gnomAD allele frequency ${fmtAf(af)} is not rare enough for PM2.`);
      notApplicable('BA1', `${benignLabel} ${fmtAf(benignAf ?? af)} is below 5%.`);
      notApplicable('BS1', `${benignLabel} ${fmtAf(benignAf ?? af)} is below the BS1 threshold.`);
    }
  } else {
    // No gnomAD allele frequency in the annotation. The variant is absent from gnomAD where its
    // callset was annotated with gnomAD's genomes or its joint set (`gnomadAbsence`): PM2
    // applies, and BA1 and BS1 are ruled out. Anywhere else nothing has looked it up across the
    // genome, so PM2 is offered for review, never applied, and BA1 / BS1 are left open (unless
    // a FAF95 rules them out).
    const absence = gnomadAbsence(variant);
    if (absence.absent) {
      add('PM2', 'supporting', absence.evidence);
      notApplicable('BA1', 'Absent from gnomAD.');
      notApplicable('BS1', 'Absent from gnomAD.');
    } else {
      add('PM2', 'supporting', absence.evidence, 'consider');
      if (benignAf != null) {
        notApplicable('BA1', `${benignLabel} ${fmtAf(benignAf)} is below 5%.`);
        notApplicable('BS1', `${benignLabel} ${fmtAf(benignAf)} is below the BS1 threshold.`);
      }
    }
  }

  // ---- BS2: observed homozygous in population; ruled out when gnomAD counts none. An
  //      annotation without a homozygote count says nothing either way, so BS2 is left
  //      unmarked for the analyst to check in gnomAD, never "No homozygotes observed"
  //      for a count the pipeline did not write. ----
  const homCount = variant.gnomad_hom_count;
  if (homCount != null && homCount > 0) {
    const recessive = isRecessiveGene(gene);
    add(
      'BS2',
      recessive ? 'strong' : 'supporting',
      `${homCount} homozygote(s) in gnomAD${
        recessive
          ? ' for a recessive-associated gene'
          : gene?.unavailable
            ? ' (the gene profile could not be loaded: confirm inheritance mode)'
            : ' (confirm inheritance mode)'
      }.`,
      recessive ? 'applies' : 'consider',
    );
  } else if (homCount === 0) {
    notApplicable('BS2', 'No homozygotes observed in gnomAD.');
  }

  // ---- PP2: missense in a gene with few benign missense variants ----
  // The gene's gnomAD missense Z stands in for the first half of PP2. Whether missense variants
  // are a common mechanism of disease in the gene, its second half, is the analyst's judgement:
  // PP2 is offered for review, never applied, and says which missense Z it read.
  const missenseZ = isMissense(effect) ? pp2MissenseZ(variant) : null;
  if (missenseZ && missenseZ.value >= PP2_MISSENSE_Z) {
    add(
      'PP2',
      'supporting',
      `Missense variant in a gene with gnomAD missense Z ${missenseZ.value.toFixed(2)} (${MISSENSE_Z_SOURCE_NOTE[missenseZ.source]}), ${PP2_MISSENSE_Z} or more: few benign missense variants. Whether missense variants are a common mechanism of disease in this gene is your judgement — review before applying.`,
      'consider',
    );
  }

  // ---- PP3 / BP4: in-silico evidence (REVEL preferred, then AlphaMissense) ----
  inSilicoSuggestions(variant).forEach((suggestion) => out.push(suggestion));

  // ---- BP7: synonymous with no predicted splice impact. Without a SpliceAI score (not
  //      computed for the variant, as for most indels and for SNVs outside the transcripts a
  //      pipeline scores) no splice impact is predicted either way, so BP7 is offered for
  //      review, never applied: a missing score is not read as no impact. ----
  if (isSynonymous(effect)) {
    if (variant.spliceai_max == null) {
      add(
        'BP7',
        'supporting',
        'Synonymous variant, but no SpliceAI score in the annotation — check the splice effect before applying.',
        'consider',
      );
    } else if (variant.spliceai_max < SPLICEAI_NO_IMPACT) {
      add('BP7', 'supporting', 'Synonymous variant with no predicted SpliceAI splice impact.');
    } else {
      against('BP7', `Synonymous, but SpliceAI ${variant.spliceai_max.toFixed(2)} predicts a possible splice effect.`);
    }
  }

  // ---- PP5 / BP6: ClinVar reputable-source assertion ----
  // A conflicting record ("Conflicting classifications of pathogenicity", or a list of
  // classifications that disagree) is neither pathogenic nor benign, so it suggests neither
  // criterion (lib/clinvar). Offered for review,
  // never applied: ClinGen's SVI advises against counting another lab's assertion as evidence
  // (the owner's choice of 2026-11-06). The evidence names the review status (its stars) and
  // the conditions ClinVar gives.
  const clinvar = clinvarClass(variant);
  const clinvarReport = describeClinvar(variant);
  if (clinvar === 'pathogenic') {
    add('PP5', 'supporting', `${clinvarReport} — review before applying.`, 'consider');
    against('BP6', `ClinVar reports ${variant.clinvar}, not benign.`);
  } else if (clinvar === 'benign') {
    add('BP6', 'supporting', `${clinvarReport} — review before applying.`, 'consider');
    against('PP5', `ClinVar reports ${variant.clinvar}, not pathogenic.`);
  }

  // ---- PP4: proband phenotype specific for the gene ----
  // Prefer the Monarch phenotype-specificity score (scales strength); fall back to
  // a direct HPO-ID overlap when no score is available (no prioritization run).
  pp4Suggestion(gene, phenotype).forEach((suggestion) => out.push(suggestion));

  // ---- Trio / segregation: de novo (PS2/PM6), cosegregation (PP1/BS4) ----
  evaluateFamily(family, add, notApplicable, hemizygousChromosome(variant), phenotype);

  // ---- Exclude criteria the molecular consequence rules out ----
  const klass = molecularClass(effect);
  const { codes, reason } = NOT_APPLICABLE[klass];
  for (const code of codes) {
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', reason, 'not_applicable');
  }

  return out;
}

// ---- gnomAD absence (PM2) ----

// The gnomAD datasets the variant's callset was annotated with (the backend's
// variant_annotation_parser.GNOMAD_ANNOTATED_KEY), as the PM2 evidence names them.
const GNOMAD_DATASET_LABELS: Record<string, string> = {
  exomes: 'gnomAD exomes',
  genomes: 'gnomAD genomes',
  joint: "gnomAD's joint exomes and genomes",
  unspecified: 'a gnomAD frequency of a set it does not name',
};

// gnomAD's genomes and its joint set cover the genome; its exomes only the exome capture
// regions, where an intronic or intergenic variant is never found.
const GNOMAD_GENOME_WIDE = new Set(['genomes', 'joint']);

// The chromosomes gnomAD's genome and exome sets hold: chrM is gnomAD-MT's own dataset, and an
// alternate or unplaced contig none of gnomAD's.
const GNOMAD_CHROMOSOMES = new Set(NUCLEAR_CHROMOSOMES);

const NO_GNOMAD_FREQUENCY = 'No gnomAD allele frequency in the annotation';

/**
 * Whether a variant without a gnomAD allele frequency is absent from gnomAD, with the PM2
 * evidence that says why or why not. Absent where its callset was annotated with gnomAD's
 * genomes or joint set (the owner's choice of 2026-11-13: VEP's cache counts as annotated, and
 * exomes alone do not), on a chromosome those sets hold, and the annotation holds no other
 * gnomAD frequency of it (popmax, FAF95). Anywhere else the annotation does not say it is
 * absent from gnomAD.
 */
export function gnomadAbsence(variant: AcmgVariantInput): { absent: boolean; evidence: string } {
  const datasets = (variant.gnomad_annotated ?? []).filter((dataset) => dataset in GNOMAD_DATASET_LABELS);
  if (!datasets.length) {
    return {
      absent: false,
      evidence: `${NO_GNOMAD_FREQUENCY}: gnomAD was not annotated for this variant's callset — check gnomAD before applying.`,
    };
  }
  const chrom = normalizeChrom(variant.chr ?? '');
  if (chrom === 'MT') {
    return {
      absent: false,
      evidence: `${NO_GNOMAD_FREQUENCY}: gnomAD's exome and genome sets hold no mtDNA variants — check gnomAD-MT before applying.`,
    };
  }
  if (!GNOMAD_CHROMOSOMES.has(chrom)) {
    return {
      absent: false,
      evidence: `${NO_GNOMAD_FREQUENCY}: gnomAD holds no variants on ${variant.chr || 'this contig'} — check gnomAD before applying.`,
    };
  }
  const annotated = datasets.map((dataset) => GNOMAD_DATASET_LABELS[dataset]).join(' and ');
  if (!datasets.some((dataset) => GNOMAD_GENOME_WIDE.has(dataset))) {
    return {
      absent: false,
      evidence:
        datasets.length === 1 && datasets[0] === 'exomes'
          ? `${NO_GNOMAD_FREQUENCY}: the callset was annotated with gnomAD exomes only, which cover the exome capture regions — check gnomAD genomes before applying.`
          : `${NO_GNOMAD_FREQUENCY}: the callset was annotated with ${annotated}, not with gnomAD genomes — check gnomAD genomes before applying.`,
    };
  }
  const frequencies = variant.population_frequencies ?? {};
  const other = ['gnomad_popmax_af', 'gnomad_faf95_af'].find((key) => typeof frequencies[key] === 'number');
  if (other) {
    return {
      absent: false,
      evidence: `${NO_GNOMAD_FREQUENCY}, though it holds a gnomAD ${other === 'gnomad_faf95_af' ? 'FAF95' : 'popmax frequency'} — check gnomAD before applying.`,
    };
  }
  return {
    absent: true,
    evidence: `Absent from gnomAD: the callset was annotated with ${annotated}, which hold no allele frequency for this variant.`,
  };
}

// 'X' or 'Y' where a male carries one copy (the backend knows the PARs), else null.
function hemizygousChromosome(variant: AcmgVariantInput): 'X' | 'Y' | null {
  if (!variant.hemizygous_in_males) return null;
  const chrom = (variant.chr ?? '').replace(/^chr/i, '').toUpperCase();
  if (chrom === 'X' || chrom === '23') return 'X';
  if (chrom === 'Y' || chrom === '24') return 'Y';
  return null;
}

const isMale = (sex?: string) => ['m', 'male', '1'].includes((sex ?? '').trim().toLowerCase());

// A reference call with too few reads to rule out an allele the parent carries.
const isShallow = (call?: AcmgFamilyMemberCall): boolean => call?.dp != null && call.dp < DE_NOVO_MIN_PARENT_DP;

// "the father's reference call has read depth 5, below the 8 …", for the shallow parents.
function shallowCallsNote(parents: { name: string; call?: AcmgFamilyMemberCall }[]): string {
  const whose = parents.map((parent) => `the ${parent.name}'s`).join(' and ');
  const depths = parents.map((parent) => parent.call?.dp).join(' and ');
  const calls = parents.length > 1 ? 'reference calls have' : 'reference call has';
  return `${whose} ${calls} read depth ${depths}, below the ${DE_NOVO_MIN_PARENT_DP} the de novo filter requires, so an inherited allele may have been missed`;
}

// Why a homozygous proband with reference parents is not a plain de novo: the event changes
// one copy, and the other copy needs its own explanation.
const HOMOZYGOUS_PROBAND_NOTE =
  'the proband is homozygous, which a de novo event alone does not explain (a deletion of the other allele, uniparental disomy or a genotyping error)';

type AddFn = (
  code: AcmgCriterionCode,
  strength: AcmgStrength,
  evidence: string,
  disposition?: AcmgDisposition,
) => void;

// ---- De novo strength (ClinGen SVI recommendation for PS2 and PM6) ----
// A de novo observation scores points by whether the parents are confirmed (PS2) or assumed
// (PM6) and by how specific the proband's phenotype is for the gene; the total sets the
// strength: 0.5 Supporting, 1 Moderate, 2 Strong, 4 Very strong. One proband reaches Strong at
// most: further de novo cases in unrelated patients add their points, which the analyst weighs.
const DE_NOVO_POINTS: Record<AcmgDeNovoPhenotypeTier, { confirmed: number; assumed: number }> = {
  highly_specific: { confirmed: 2, assumed: 1 },
  consistent: { confirmed: 1, assumed: 0.5 },
  heterogeneous: { confirmed: 0.5, assumed: 0.25 },
  not_consistent: { confirmed: 0, assumed: 0 },
};

const DE_NOVO_TIER_LABELS: Record<AcmgDeNovoPhenotypeTier, string> = {
  highly_specific: 'highly specific for the gene',
  consistent: 'consistent with the gene but not highly specific',
  heterogeneous: 'consistent with the gene only in general terms, as a genetically heterogeneous phenotype is',
  not_consistent: 'not shown consistent with the gene',
};

/** The strength ClinGen's de novo points reach, or null below the 0.5 of Supporting. */
export function deNovoStrength(points: number): AcmgStrength | null {
  if (points >= 4) return 'very_strong';
  if (points >= 2) return 'strong';
  if (points >= 1) return 'moderate';
  if (points >= 0.5) return 'supporting';
  return null;
}

/**
 * How specific the proband's phenotype is for the gene, for the de novo points, read from the
 * gene–phenotype match score (normalised Resnik best-match average, 0 to 1) on PP4's cut-offs:
 * 0.6 or more highly specific (PP4 Moderate), 0.3 or more consistent but not highly specific
 * (PP4 Supporting), above 0 a match on general terms only, as a genetically heterogeneous
 * phenotype (intellectual disability, seizures) gives, and 0 not shown consistent. The score
 * weighs each matched term by how few diseases share it, so generic terms score low. Null
 * without a score: not assessed.
 */
export function deNovoPhenotypeTier(
  phenotype?: AcmgPhenotypeContext,
): { tier: AcmgDeNovoPhenotypeTier; score: number } | null {
  const score = phenotype?.phenotypeScore;
  if (typeof score !== 'number' || !Number.isFinite(score)) return null;
  const tier: AcmgDeNovoPhenotypeTier =
    score >= PP4_MODERATE_SCORE
      ? 'highly_specific'
      : score >= PP4_SUPPORTING_SCORE
        ? 'consistent'
        : score > 0
          ? 'heterogeneous'
          : 'not_consistent';
  return { tier, score };
}

const capitalize = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;

const formatPoints = (points: number) => `${points} point${points === 1 ? '' : 's'}`;

// What the parents' calls show of a de novo variant, from evaluateFamily.
type DeNovoObservation = {
  // "Absent in both sequenced parents", "Absent in the mother, who passes a son his X".
  finding: string;
  // The parents whose parentage makes it confirmed: both, or for a son's X or Y the parent who
  // passes it on.
  parents: Array<{ id: string; name: 'father' | 'mother' }>;
  // Why the observation needs review (a shallow parent call, a homozygous proband).
  caveats: string[];
};

type DeNovoContext = {
  family: AcmgFamilyContext | undefined;
  proband: AcmgFamilyMemberCall | undefined;
  parentIds: { father?: string; mother?: string };
  phenotype: AcmgPhenotypeContext | undefined;
};

// "maternity and paternity", "maternity", "paternity".
const parentageNoun = (parents: DeNovoObservation['parents']) =>
  parents.map((parent) => (parent.name === 'mother' ? 'maternity' : 'paternity')).sort().join(' and ');

// Why the Sample QC does not confirm the parents.
function parentageNotConfirmed(
  checks: Array<{ name: string; check?: AcmgParentageCheck }>,
  family: AcmgFamilyContext | undefined,
): string {
  if (family?.parentageUnavailable) return 'the Sample QC could not be loaded';
  if (!family?.parentage) return 'the Sample QC is not loaded';
  const open = checks.find((parent) => parent.check?.status !== 'confirmed');
  if (!open) return 'the Sample QC does not confirm it';
  return open.check
    ? `the Sample QC's check of the proband and the ${open.name} is not a pass (${open.check.message})`
    : `the Sample QC has no parent-child check of the proband and the ${open.name}`;
}

// What the phenotype part of the de novo points cannot be read from.
function phenotypeNotAssessed(phenotype: AcmgPhenotypeContext | undefined): string {
  if (phenotype?.probandHpoUnavailable) return "the family's HPO terms could not be loaded";
  if (!phenotype?.probandHpoIds?.length) return 'the proband has no HPO terms';
  return 'there is no gene–phenotype match score for the gene';
}

/**
 * PS2 or PM6 for a de novo observation, at the strength ClinGen's de novo points give it.
 *
 * PS2 where the Sample QC confirms the parents whose calls make it de novo, PM6 otherwise; the
 * other one is marked not applicable. A parent the Sample QC finds is not the proband's parent
 * argues against both. Applied where the phenotype is scored and reaches Supporting and nothing
 * casts doubt on the observation; offered for review otherwise: a shallow parent call, a
 * homozygous proband, a proband not recorded as affected, a parent recorded as affected (the
 * criteria assume no family history), a phenotype not scored, or too few points.
 */
function suggestDeNovo(
  observation: DeNovoObservation,
  { family, proband, parentIds, phenotype }: DeNovoContext,
  add: AddFn,
  notApplicable: (code: AcmgCriterionCode, evidence: string) => void,
): void {
  const { finding, parents } = observation;
  const checks = parents.map((parent) => ({ ...parent, check: family?.parentage?.[parent.id] }));
  const refuted = checks.find((parent) => parent.check?.status === 'refuted');
  if (refuted) {
    const evidence = `${finding}, but the Sample QC finds the proband and the ${refuted.name} are not parent and child (${refuted.check?.message}) — resolve the Sample QC before assessing de novo.`;
    add('PS2', 'strong', evidence, 'contraindicated');
    add('PM6', 'moderate', evidence, 'contraindicated');
    return;
  }
  const confirmed = checks.every((parent) => parent.check?.status === 'confirmed');
  const code: AcmgCriterionCode = confirmed ? 'PS2' : 'PM6';
  const why = confirmed ? '' : parentageNotConfirmed(checks, family);
  notApplicable(
    confirmed ? 'PM6' : 'PS2',
    confirmed
      ? `${capitalize(parentageNoun(parents))} confirmed by the Sample QC: PS2, not PM6.`
      : `Parentage not confirmed (${why}): PM6, not PS2.`,
  );

  const members = family?.members ?? [];
  const affectedParents = (['father', 'mother'] as const).filter((name) =>
    members.some((member) => member.sampleId === parentIds[name] && member.affected),
  );
  const caveats = [
    ...observation.caveats,
    proband && !proband.affected ? 'the proband is not recorded as affected' : null,
    ...affectedParents.map(
      (name) => `the ${name} is recorded as affected, and the de novo criteria assume no family history`,
    ),
  ].filter((caveat): caveat is string => Boolean(caveat));

  const opening = `${finding} (de novo), ${
    confirmed ? `${parentageNoun(parents)} confirmed by the Sample QC` : `parentage not confirmed: ${why}`
  }${caveats.length ? `, but ${caveats.join(', and ')}` : ''}.`;
  const review = ' — review before applying.';

  const scored = deNovoPhenotypeTier(phenotype);
  if (!scored) {
    add(
      code,
      confirmed ? 'moderate' : 'supporting',
      `${opening} The phenotype's specificity for the gene is not assessed (${phenotypeNotAssessed(phenotype)}): set the strength by ClinGen's de novo points${review}`,
      'consider',
    );
    return;
  }
  const points = DE_NOVO_POINTS[scored.tier][confirmed ? 'confirmed' : 'assumed'];
  const strength = deNovoStrength(points);
  const scoring = `Phenotype ${DE_NOVO_TIER_LABELS[scored.tier]} (gene–phenotype match ${scored.score.toFixed(2)}): ${formatPoints(points)} by ClinGen's de novo points`;
  if (!strength) {
    const condition = points > 0 ? 'with further de novo cases in unrelated patients' : 'where the phenotype fits the gene';
    add(code, 'supporting', `${opening} ${scoring}, below the 0.5 of Supporting: apply only ${condition}${review}`, 'consider');
  } else if (caveats.length) {
    add(code, strength, `${opening} ${scoring}, ${STRENGTH_LABELS[strength]}${review}`, 'consider');
  } else {
    add(code, strength, `${opening} ${scoring}, ${STRENGTH_LABELS[strength]}.`, 'applies');
  }
}

function evaluateFamily(
  family: AcmgFamilyContext | undefined,
  add: AddFn,
  notApplicable: (code: AcmgCriterionCode, evidence: string) => void,
  hemizygousOn: 'X' | 'Y' | null,
  phenotype: AcmgPhenotypeContext | undefined,
): void {
  const members = family?.members ?? [];
  const byRole = (role: string) =>
    members.find((m) => (m.role ?? '').toLowerCase() === role);
  const proband = byRole('proband') ?? members.find((m) => m.affected);
  const probandCarries = proband ? (altAlleleCount(proband.gt) ?? 0) > 0 : false;

  // The proband's parents are the members the pedigree links to them as father and mother,
  // as in the backend's de novo mode (_parent_child_links). Not the members with the role
  // 'father' or 'mother': a grandparent has that role too (ped_service), and a lookup by role
  // could compare the proband with them. A linked parent who is not sequenced has no call.
  const parentIds = proband ? parentsOf(proband.sampleId, family?.parentLinks) : {};
  const callOf = (sampleId?: string) => (sampleId ? members.find((m) => m.sampleId === sampleId) : undefined);
  const father = callOf(parentIds.father);
  const mother = callOf(parentIds.mother);
  const fa = father ? altAlleleCount(father.gt) : null;
  const ma = mother ? altAlleleCount(mother.gt) : null;

  // ---- De novo (PS2 / PM6) ----
  // A de novo observation needs the parents' calls to be reference with enough reads to trust
  // them (DE_NOVO_MIN_PARENT_DP) and, on an autosome, in a PAR and in a daughter, the proband's
  // call to be heterozygous, as in the backend's de novo mode (_record_matches_de_novo);
  // otherwise it is offered for review, with the reason. A son's hemizygous call is an ALT of
  // either ploidy. suggestDeNovo then decides PS2 or PM6 and the strength.
  if (probandCarries && hemizygousOn && isMale(proband?.sex)) {
    // A son is hemizygous here: his X comes from his mother and his Y from his father,
    // so that parent decides. The other parent need not be genotyped, but must not carry
    // the ALT: an ALT in both males points to an artifact. Mirrors the backend's de novo
    // segregation mode (_record_matches_de_novo).
    const fromMother = hemizygousOn === 'X';
    const transmittingId = fromMother ? parentIds.mother : parentIds.father;
    const transmitting = fromMother ? mother : father;
    const other = fromMother ? father : mother;
    const transmittingName = fromMother ? 'mother' : 'father';
    const otherName = fromMother ? 'father' : 'mother';
    const passes = `who passes a son his ${hemizygousOn}`;
    const ta = transmitting ? altAlleleCount(transmitting.gt) : null;
    const oa = other ? altAlleleCount(other.gt) : null;
    if (!transmittingId) {
      const unlinked = `The pedigree does not name the proband's ${transmittingName}, ${passes} — de novo cannot be assessed.`;
      notApplicable('PS2', unlinked);
      notApplicable('PM6', unlinked);
    } else if (ta == null) {
      const missing = `The ${transmittingName}'s genotype, ${passes}, is missing — de novo cannot be assessed.`;
      notApplicable('PS2', missing);
      notApplicable('PM6', missing);
    } else if (ta > 0) {
      const inherited = `Inherited from the ${transmittingName}, ${passes} — not de novo.`;
      notApplicable('PS2', inherited);
      notApplicable('PM6', inherited);
    } else if (oa != null && oa > 0) {
      const artifact = `Also called in the ${otherName}, who does not pass a son his ${hemizygousOn}: likely an artifact, not called de novo.`;
      notApplicable('PS2', artifact);
      notApplicable('PM6', artifact);
    } else {
      suggestDeNovo(
        {
          finding: `Absent in the ${transmittingName}, ${passes}`,
          parents: [{ id: transmittingId, name: transmittingName }],
          caveats: isShallow(transmitting) ? [shallowCallsNote([{ name: transmittingName, call: transmitting }])] : [],
        },
        { family, proband, parentIds, phenotype },
        add,
        notApplicable,
      );
    }
  } else if (probandCarries && !(parentIds.father && parentIds.mother)) {
    const unlinked = 'The pedigree does not name both parents of the proband — de novo cannot be assessed.';
    notApplicable('PS2', unlinked);
    notApplicable('PM6', unlinked);
  } else if (probandCarries && fa != null && ma != null && parentIds.father && parentIds.mother) {
    if (fa === 0 && ma === 0) {
      const shallow = [
        { name: 'father', call: father },
        { name: 'mother', call: mother },
      ].filter((parent) => isShallow(parent.call));
      const caveats = [
        classifyGenotype(proband?.gt) === 'hom_alt' ? HOMOZYGOUS_PROBAND_NOTE : null,
        shallow.length ? shallowCallsNote(shallow) : null,
      ].filter((caveat): caveat is string => Boolean(caveat));
      suggestDeNovo(
        {
          finding: 'Absent in both sequenced parents',
          parents: [
            { id: parentIds.father, name: 'father' },
            { id: parentIds.mother, name: 'mother' },
          ],
          caveats,
        },
        { family, proband, parentIds, phenotype },
        add,
        notApplicable,
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
  // Counted by person: two samples of one person (a same-individual link) are one member. A
  // person one of whose samples carries the variant is a carrier.
  const personOf = (sampleId: string): string => family?.personOf?.[sampleId] ?? sampleId;
  const affectedCarriers = new Set(
    members.filter((m) => m.affected && (altAlleleCount(m.gt) ?? 0) > 0).map((m) => personOf(m.sampleId)),
  );
  const affectedNonCarriers = new Set(
    members
      .filter((m) => m.affected && altAlleleCount(m.gt) === 0)
      .map((m) => personOf(m.sampleId))
      .filter((person) => !affectedCarriers.has(person)),
  );
  if (affectedCarriers.size >= 2) {
    add(
      'PP1',
      'supporting',
      `Variant carried by ${affectedCarriers.size} affected family members (cosegregation).`,
      'consider',
    );
  } else {
    notApplicable('PP1', 'No additional affected carriers — cosegregation cannot be shown.');
  }
  if (affectedNonCarriers.size >= 1) {
    add(
      'BS4',
      'strong',
      `${affectedNonCarriers.size} affected family member(s) do not carry the variant (lack of segregation).`,
      'consider',
    );
  } else {
    notApplicable('BS4', 'No affected relative lacking the variant — lack of segregation cannot be shown.');
  }
}

/**
 * PP4 when the lookup it needs failed: surfaced for review, never auto-applied, and never
 * read as "no phenotype match".
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

// What casts doubt on a predicted null effect, for PVS1: LOFTEE's flags (SINGLE_EXON,
// NAGNAG_SITE, PHYLOCSF_WEAK …) and VEP's NMD plugin predicting an escape from
// nonsense-mediated decay. Null when neither.
export function pvs1Caution(variant: Pick<AcmgVariantInput, 'lof_flags' | 'nmd'>): string | null {
  const reasons: string[] = [];
  const flags = (variant.lof_flags ?? '').trim();
  if (flags) reasons.push(`LOFTEE flags it (${flags.replace(/&/g, ', ')})`);
  if ((variant.nmd ?? '').toLowerCase().includes('escap')) reasons.push('it is predicted to escape nonsense-mediated decay');
  return reasons.length ? reasons.join(' and ') : null;
}

// ClinVar's assertion as the PP5 / BP6 evidence says it: the classification, the review
// status with its stars, and the conditions.
export function describeClinvar(
  variant: Pick<AcmgVariantInput, 'clinvar' | 'clinvar_review_status' | 'clinvar_stars' | 'clinvar_conditions'>,
): string {
  const stars = variant.clinvar_stars;
  const status = variant.clinvar_review_status
    ? `${typeof stars === 'number' ? `${stars} star${stars === 1 ? '' : 's'}, ` : ''}${variant.clinvar_review_status}`
    : null;
  const conditions = (variant.clinvar_conditions ?? []).filter(Boolean);
  return `ClinVar reports ${variant.clinvar}${status ? ` (${status})` : ''}${
    conditions.length ? ` for ${conditions.slice(0, 3).join('; ')}${conditions.length > 3 ? ' …' : ''}` : ''
  }`;
}

// In-silico evidence: a positive PP3/BP4 suggestion plus an explicit
// contraindication of the opposite criterion when the signal is clear.
function inSilicoSuggestions(variant: AcmgVariantInput): AcmgSuggestion[] {
  const { revel } = variant;
  // BP4 needs SpliceAI to predict no splice impact. Without a score it predicts nothing:
  // BP4 is then offered for review, never applied. A missing score read as 0 applied BP4
  // with "SpliceAI 0.00" as its evidence.
  const spliceScored = variant.spliceai_max != null;
  const splice = variant.spliceai_max ?? 0;
  const noSpliceScore = 'no SpliceAI score in the annotation — check the splice effect before applying';

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
        spliceScored
          ? {
              code: 'BP4',
              strength,
              evidence: `REVEL ${revel.toFixed(3)} and SpliceAI ${splice.toFixed(2)} support no functional impact.`,
              disposition: 'applies',
            }
          : {
              code: 'BP4',
              strength,
              evidence: `REVEL ${revel.toFixed(3)} supports no functional impact, but ${noSpliceScore}.`,
              disposition: 'consider',
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

  // The served field, not annotation_extra: that holds the annotation's own key
  // (alpha_missense_class), so reading `alphamissense_class` there never found a class.
  const amClass = String(variant.alpha_missense_class ?? '').toLowerCase();
  if (amClass.includes('pathogenic')) {
    return [{ code: 'PP3', strength: 'supporting', evidence: 'AlphaMissense class: likely pathogenic.', disposition: 'applies' }];
  }
  if (amClass.includes('benign')) {
    return [
      spliceScored
        ? { code: 'BP4', strength: 'supporting', evidence: 'AlphaMissense class: likely benign.', disposition: 'applies' }
        : {
            code: 'BP4',
            strength: 'supporting',
            evidence: `AlphaMissense class: likely benign, but ${noSpliceScore}.`,
            disposition: 'consider',
          },
    ];
  }

  // No in-silico prediction at all → the computational criteria are ruled out. Any
  // AlphaMissense class is a prediction: an ambiguous one is inconclusive, and leaves both
  // open below, never read as no prediction that rules them out.
  const hasAnyPrediction = revel != null || variant.spliceai_max != null || amClass.trim() !== '';
  if (!hasAnyPrediction) {
    const na = (code: AcmgCriterionCode): AcmgSuggestion => ({
      code,
      strength: 'supporting',
      evidence: 'No in-silico predictions available.',
      disposition: 'not_applicable',
    });
    return [na('PP3'), na('BP4')];
  }

  // Predictions exist but are inconclusive (REVEL in the grey zone, an ambiguous AlphaMissense
  // class) → leave neutral.
  return [];
}

const STRENGTH_ORDER: AcmgStrength[] = ['supporting', 'moderate', 'strong', 'very_strong'];

function strongerOf(a: AcmgStrength, b: AcmgStrength | null): AcmgStrength {
  if (!b) return a;
  return STRENGTH_ORDER.indexOf(a) >= STRENGTH_ORDER.indexOf(b) ? a : b;
}
