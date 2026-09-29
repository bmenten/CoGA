// mtDNA-specific ACMG/AMP auto-evaluator (McCormick 2020 specifications).
//
// Mitochondrial DNA is haploid and maternally inherited, so several nuclear
// assumptions break: there is no de novo trio logic, frequency thresholds are
// stricter, PVS1 only applies inside protein-coding loci, and the usual nuclear
// in-silico predictors (REVEL/SpliceAI) are not computed for MT. This evaluator
// mirrors the nuclear `evaluateAcmg` contract (returns AcmgSuggestion[]) so the
// modal, scorer and `buildInitialSelections` are shared unchanged.

import { clinvarClass } from '../clinvar';
import { ACMG_CRITERIA_BY_CODE } from './criteria';
import { effectIncludes, fmtAf, LOF_EFFECTS, pp4NotAssessed, type AcmgVariantInput } from './evaluate';
import type {
  AcmgCriterionCode,
  AcmgDisposition,
  AcmgGeneContext,
  AcmgMitoContext,
  AcmgPhenotypeContext,
  AcmgStrength,
  AcmgSuggestion,
} from './types';

// mtDNA gnomAD-MT allele-frequency cut-offs (stricter than nuclear, per
// McCormick 2020 — common mt variants/haplogroup markers sit well below 5%).
const MT_BA1_AF = 0.005; // stand-alone benign (common mt polymorphism)
const MT_BS1_AF = 2e-4; // higher than expected for a primary mt disorder
const MT_PM2_AF = 2e-5; // rare enough to support pathogenicity

// Alt-allele-bearing zygosity states (homoplasmic / heteroplasmic / low-level).
const CARRIER_ZYGOSITY = new Set(['homoplasmic', 'heteroplasmic', 'low_level']);

function clinSigIncludes(value: string | null | undefined, needles: string[]): boolean {
  if (!value) return false;
  const lower = value.toLowerCase();
  return needles.some((needle) => lower.includes(needle));
}

export function evaluateMitoAcmg(
  variant: AcmgVariantInput,
  mito: AcmgMitoContext,
  gene?: AcmgGeneContext,
  phenotype?: AcmgPhenotypeContext,
): AcmgSuggestion[] {
  const out: AcmgSuggestion[] = [];
  const add = (
    code: AcmgCriterionCode,
    strength: AcmgStrength,
    evidence: string,
    disposition: AcmgDisposition = 'applies',
  ) => out.push({ code, strength, evidence, disposition });
  const notApplicable = (code: AcmgCriterionCode, evidence: string) =>
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', evidence, 'not_applicable');
  const against = (code: AcmgCriterionCode, evidence: string) =>
    add(code, ACMG_CRITERIA_BY_CODE[code]?.defaultStrength ?? 'supporting', evidence, 'contraindicated');

  const effect = variant.effect;
  const af = variant.gnomad_af;
  const category = mito.category ?? null;
  const clinSig = mito.clinicalSignificance ?? null;

  // ---- PVS1: predicted null, only inside a protein-coding mt gene ----
  if (effectIncludes(effect, LOF_EFFECTS)) {
    if (category === 'protein coding') {
      add('PVS1', 'very_strong', `Predicted null (${effect}) in protein-coding mt gene ${variant.gene ?? ''}.`.trim());
    } else if (category === 'tRNA' || category === 'rRNA' || category === 'control') {
      notApplicable('PVS1', `PVS1 does not apply to a ${category} locus (no protein product).`);
    } else {
      add(
        'PVS1',
        'strong',
        `Predicted null (${effect}), but the mt locus/category is unresolved — confirm it is protein-coding before applying.`,
        'consider',
      );
    }
  } else if (category === 'tRNA' || category === 'rRNA' || category === 'control') {
    notApplicable('PVS1', `PVS1 does not apply to a ${category} locus.`);
  }

  // ---- Frequency: BA1 / BS1 / PM2 on gnomAD-MT (mt-specific thresholds) ----
  const isPolymorphism = clinSigIncludes(clinSig, ['polymorphism']);
  if (af != null && af >= MT_BA1_AF) {
    add('BA1', 'stand_alone', `gnomAD-MT allele frequency ${fmtAf(af)} ≥ 0.5% — common mt polymorphism (stand-alone benign).`);
    notApplicable('PM2', `Not rare: gnomAD-MT allele frequency ${fmtAf(af)}.`);
  } else if ((af != null && af >= MT_BS1_AF) || isPolymorphism) {
    const reason =
      af != null && af >= MT_BS1_AF
        ? `gnomAD-MT allele frequency ${fmtAf(af)} is higher than expected for a primary mt disorder.`
        : 'MITOMAP reports a common polymorphism / haplogroup marker.';
    add('BS1', 'strong', reason);
    notApplicable('BA1', 'Below the mt stand-alone benign threshold (0.5%).');
    notApplicable('PM2', 'Too common for PM2.');
  } else if (af == null || af < MT_PM2_AF) {
    add('PM2', 'supporting', af == null ? 'Absent from gnomAD-MT.' : `gnomAD-MT allele frequency ${fmtAf(af)} is very rare.`);
    notApplicable('BA1', 'Rare in gnomAD-MT.');
    notApplicable('BS1', 'Rare in gnomAD-MT.');
  } else {
    notApplicable('PM2', `gnomAD-MT allele frequency ${fmtAf(af)} is not rare enough for PM2.`);
    notApplicable('BA1', `gnomAD-MT allele frequency ${fmtAf(af)} is below 0.5%.`);
    notApplicable('BS1', `gnomAD-MT allele frequency ${fmtAf(af)} is below the BS1 threshold.`);
  }

  // ---- PP5 / BP6: MITOMAP / ClinVar reputable-source assertion ----
  // Read as the nuclear evaluator reads ClinVar (lib/clinvar): a conflicting record is neither
  // pathogenic nor benign. The backend already reads one as 'uncertain'.
  const disorders = (mito.disorders ?? []).filter(Boolean);
  const disorderNote = disorders.length ? ` Associated: ${disorders.slice(0, 3).join(', ')}.` : '';
  const clinClass = clinvarClass(clinSig);
  if (clinClass === 'pathogenic') {
    add('PP5', 'supporting', `MITOMAP/ClinVar reports ${clinSig}.${disorderNote}`);
    against('BP6', `MITOMAP/ClinVar reports ${clinSig}, not benign.`);
  } else if (clinClass === 'benign' || isPolymorphism) {
    add('BP6', 'supporting', `MITOMAP/ClinVar reports ${clinSig}.`);
    against('PP5', `MITOMAP/ClinVar reports ${clinSig}, not pathogenic.`);
  }

  // ---- PP3 / BP4: no mt in-silico predictor wired in ----
  notApplicable('PP3', 'No mt-specific in-silico predictor loaded (MitoTIP / APOGEE / HmtVar) — assess manually.');
  notApplicable('BP4', 'No mt-specific in-silico predictor loaded (MitoTIP / APOGEE / HmtVar) — assess manually.');

  // ---- PM1: tRNA functional/structural domain ----
  if (category === 'tRNA') {
    add('PM1', 'moderate', `Variant in tRNA locus ${variant.gene ?? ''} — review the tRNA functional/structural domain (PM1).`.trim(), 'consider');
  } else {
    notApplicable('PM1', 'No mt hot-spot/domain annotation for this locus.');
  }

  // ---- PS2 / PM6: not applicable to maternally-inherited mtDNA ----
  notApplicable('PS2', 'mtDNA is maternally inherited — de novo criteria do not apply; assess maternal segregation instead.');
  notApplicable('PM6', 'mtDNA is maternally inherited — de novo criteria do not apply.');

  // ---- PP1 / BS4: maternal segregation + heteroplasmy ----
  evaluateMaternalSegregation(mito, add, notApplicable);

  // ---- PP4: proband phenotype specific for the gene, with heteroplasmy support ----
  evaluatePp4(variant, mito, gene, phenotype, add);

  // ---- Nuclear-only criteria that cannot apply to a haploid mt variant ----
  for (const code of NUCLEAR_ONLY_NA) {
    notApplicable(code, 'Not applicable to mitochondrial (haploid, maternally-inherited) variants.');
  }

  return out;
}

const NUCLEAR_ONLY_NA: AcmgCriterionCode[] = ['PP2', 'PM5', 'PM3', 'PM4', 'BP1', 'BP2', 'BP3', 'BP7', 'BS2'];

type MitoAddFn = (
  code: AcmgCriterionCode,
  strength: AcmgStrength,
  evidence: string,
  disposition?: AcmgDisposition,
) => void;

function evaluateMaternalSegregation(
  mito: AcmgMitoContext,
  add: MitoAddFn,
  notApplicable: (code: AcmgCriterionCode, evidence: string) => void,
): void {
  const calls = mito.calls ?? [];
  const maternal = (mito.maternalTransmission ?? '').toLowerCase();
  const maternalShared = maternal.includes('maternal');

  const affectedCarriers = calls.filter(
    (c) => c.affected && c.zygosity && CARRIER_ZYGOSITY.has(c.zygosity),
  );
  const affectedNonCarriers = calls.filter((c) => c.affected && c.zygosity === 'reference');

  if (affectedCarriers.length >= 2 && maternalShared) {
    add(
      'PP1',
      'supporting',
      `Maternally transmitted and carried by ${affectedCarriers.length} affected maternal-line relatives (heteroplasmy concordant).`,
      'consider',
    );
  } else {
    notApplicable('PP1', 'Maternal cosegregation cannot be shown (need ≥2 affected maternal-line carriers).');
  }

  if (affectedNonCarriers.length >= 1) {
    add(
      'BS4',
      'strong',
      `${affectedNonCarriers.length} affected maternal relative(s) do not carry the variant — lack of segregation.`,
      'consider',
    );
  } else {
    notApplicable('BS4', 'No affected relative lacking the variant — lack of segregation cannot be shown.');
  }
}

function evaluatePp4(
  variant: AcmgVariantInput,
  mito: AcmgMitoContext,
  gene: AcmgGeneContext | undefined,
  phenotype: AcmgPhenotypeContext | undefined,
  add: MitoAddFn,
): void {
  const notAssessed = pp4NotAssessed(gene, phenotype);
  if (notAssessed) {
    add(notAssessed.code, notAssessed.strength, notAssessed.evidence, notAssessed.disposition);
    return;
  }
  const probandHpo = phenotype?.probandHpoIds ?? [];
  const geneHpo = new Set(gene?.geneHpoIds ?? []);
  const overlap = probandHpo.filter((id) => geneHpo.has(id));
  if (!overlap.length) return;

  // A clear proband heteroplasmy/homoplasmy level strengthens the gene-specific
  // phenotype interpretation (heteroplasmy correlates with mt disease burden).
  const probandCall = (mito.calls ?? []).find(
    (c) => (c.role ?? '').toLowerCase() === 'proband' || c.affected,
  );
  const zyg = probandCall?.zygosity;
  const carries = zyg ? CARRIER_ZYGOSITY.has(zyg) : false;
  const afNote = probandCall?.alleleFraction != null
    ? ` (heteroplasmy ${(probandCall.alleleFraction * 100).toFixed(0)}%)`
    : '';

  add(
    'PP4',
    'supporting',
    `Proband HPO term(s) overlap the gene's phenotype (${overlap.length} shared)${carries ? `; proband carries the variant${afNote}` : ''}.`,
    'applies',
  );
}
