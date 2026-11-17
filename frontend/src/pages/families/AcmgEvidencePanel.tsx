import {
  deNovoPhenotypeTier,
  parentsOf,
  type AcmgDeNovoPhenotypeTier,
  type AcmgFamilyContext,
  type AcmgGeneContext,
  type AcmgParentageCheck,
} from '../../lib/acmg';
import type { FamilyGenePhenotypeScoreOut } from '../../lib/apiSchema.generated';
import type { AcmgModalVariant } from './AcmgClassificationModal';
import {
  alphaMissenseTone,
  caddTone,
  polyphenTone,
  revelTone,
  siftTone,
  spliceAiTone,
  type PredictorTone,
} from './inSilicoTones';
import PredictorMark, { ALPHA_MISSENSE_CLASS_READING, SEQR_THRESHOLDS, SPLICEAI_THRESHOLDS } from './PredictorMark';
import {
  SEGREGATION_MODE_LABELS,
  formatClinvarStars,
  formatFrequency,
  formatPredictionScore,
  formatScore,
  formatSpliceAi,
  formatTokenLabel,
} from './smallVariantResultUtils';

// The data the analyst weighs the ACMG criteria on, beside the criteria in the classification
// dialog: the consequence, the population frequencies, the in silico predictions, the gene, ClinVar,
// the family's calls and the proband's phenotype. Each section names the criteria it informs. A
// value the annotation does not hold reads "—", never a value that would argue for or against a
// criterion. Each in silico prediction has the coloured mark the variant card gives it.

type EvidenceRow = {
  label: string;
  value: string;
  // How a predictor's value reads, for the mark before it (PredictorMark).
  tone?: PredictorTone | null;
  basis?: string;
};

type EvidenceSection = {
  title: string;
  criteria: string;
  rows: EvidenceRow[];
};

type AcmgEvidencePanelProps = {
  variant: AcmgModalVariant;
  geneContext?: AcmgGeneContext;
  familyContext: AcmgFamilyContext;
  probandHpoLabels: string[];
  probandHpoUnavailable: boolean;
  // The family's phenotype score for the gene, where no ranking scored the variant.
  genePhenotypeScore?: FamilyGenePhenotypeScoreOut;
};

const NONE = '—';

const finite = (value: unknown): number | null =>
  typeof value === 'number' && Number.isFinite(value) ? value : null;

const text = (value: string | null | undefined): string => (value && value.trim() ? value : NONE);

// A row that is always listed: its absence is evidence too (PM2 reads a missing frequency).
const row = (label: string, value: string): EvidenceRow => ({ label, value });

// A predictor's row, with the mark for how its value reads (none for a value it does not hold).
const predictorRow = (label: string, value: string, tone: PredictorTone | null, basis: string): EvidenceRow => ({
  label,
  value,
  tone: value === NONE ? null : tone,
  basis,
});

// A row listed only where the annotation holds a value.
const optionalRow = (label: string, value: string | null | undefined): EvidenceRow | null =>
  value && value !== NONE ? { label, value } : null;

const present = (rows: Array<EvidenceRow | null>): EvidenceRow[] =>
  rows.filter((entry): entry is EvidenceRow => Boolean(entry));

const unique = (values: string[]): string[] => Array.from(new Set(values));

const consequenceSection = (variant: AcmgModalVariant): EvidenceSection => {
  const transcriptTags = [
    variant.mane_select ? 'MANE Select' : null,
    variant.mane_plus_clinical ? 'MANE Plus Clinical' : null,
    variant.canonical ? 'canonical' : null,
  ].filter(Boolean);
  const transcript = variant.transcript_id
    ? `${variant.transcript_id}${transcriptTags.length ? ` (${transcriptTags.join(', ')})` : ''}`
    : NONE;
  const consequence = formatTokenLabel(variant.effect);
  const lof = variant.lof
    ? [variant.lof, variant.lof_filter, variant.lof_flags].filter(Boolean).join(' · ')
    : null;
  const mito = variant.chr === 'MT' ? variant.mito : null;
  return {
    title: 'Consequence',
    criteria: 'PVS1 · PM1 · PM4 · BP1 · BP3 · BP7',
    rows: present([
      row('Consequence', variant.impact ? `${consequence} (${variant.impact})` : consequence),
      row('HGVS c.', text(variant.hgvsc)),
      row('HGVS p.', text(variant.hgvsp)),
      row('Transcript', transcript),
      optionalRow('Exon / intron', variant.exon ? `exon ${variant.exon}` : variant.intron ? `intron ${variant.intron}` : null),
      optionalRow('LoF (LOFTEE)', lof),
      optionalRow('NMD', variant.nmd ? formatTokenLabel(variant.nmd) : null),
      optionalRow('mtDNA locus', mito?.category),
    ]),
  };
};

const populationSection = (variant: AcmgModalVariant): EvidenceSection => {
  const frequencies = variant.population_frequencies ?? {};
  const cohort = variant.internal_cohort;
  const mito = variant.chr === 'MT' ? variant.mito : null;
  const mitoFrequency = (af: number | null | undefined, source: string | null | undefined) =>
    finite(af) === null ? NONE : `${formatFrequency(af)}${source ? ` (${source})` : ''}`;
  return {
    title: 'Population frequency',
    criteria: 'BA1 · BS1 · BS2 · PM2',
    rows: present([
      ...(mito
        ? [
            row('Homoplasmic', mitoFrequency(mito.homoplasmicAf, mito.homoplasmicSource)),
            row('Heteroplasmic', mitoFrequency(mito.heteroplasmicAf, mito.heteroplasmicSource)),
          ]
        : []),
      row('gnomAD AF', formatFrequency(variant.gnomad_af)),
      // BA1 and BS1 read the filtering allele frequency where it is annotated.
      mito ? null : row('gnomAD FAF95', formatFrequency(frequencies.gnomad_faf95_af)),
      optionalRow('gnomAD popmax', formatFrequency(frequencies.gnomad_popmax_af)),
      optionalRow('gnomAD exomes', formatFrequency(frequencies.gnomad_exomes_af)),
      optionalRow('gnomAD genomes', formatFrequency(frequencies.gnomad_genomes_af)),
      optionalRow('MAX_AF (VEP)', formatFrequency(frequencies.max_af)),
      optionalRow('TOPMed', formatFrequency(frequencies.topmed_af)),
      mito ? null : row('gnomAD homozygotes', finite(variant.gnomad_hom_count) === null ? NONE : String(variant.gnomad_hom_count)),
      optionalRow('gnomAD datasets', variant.gnomad_annotated?.length ? variant.gnomad_annotated.join(', ') : null),
      cohort
        ? row(
            'In-house cohort',
            `${cohort.samples} sample${cohort.samples === 1 ? '' : 's'} (${cohort.het} het / ${cohort.hom} hom) · ${cohort.families} famil${cohort.families === 1 ? 'y' : 'ies'}`,
          )
        : null,
    ]),
  };
};

const inSilicoSection = (variant: AcmgModalVariant): EvidenceSection => {
  const amScore = finite(variant.alpha_missense_pathogenicity);
  const amClass = variant.alpha_missense_class ? formatTokenLabel(variant.alpha_missense_class) : null;
  const alphaMissense =
    amScore !== null && amClass ? `${formatScore(amScore, 3)} · ${amClass}` : amScore !== null ? formatScore(amScore, 3) : amClass ?? NONE;
  return {
    title: 'In silico',
    criteria: 'PP3 · BP4 · BP7',
    rows: present([
      predictorRow('REVEL', formatScore(variant.revel), revelTone(variant.revel), SEQR_THRESHOLDS),
      predictorRow('CADD', formatScore(variant.cadd_phred), caddTone(variant.cadd_phred), SEQR_THRESHOLDS),
      predictorRow(
        'AlphaMissense',
        alphaMissense,
        alphaMissenseTone(variant.alpha_missense_pathogenicity, variant.alpha_missense_class),
        amScore !== null ? SEQR_THRESHOLDS : ALPHA_MISSENSE_CLASS_READING,
      ),
      predictorRow('SpliceAI', formatSpliceAi({
        spliceai_max: variant.spliceai_max ?? null,
        spliceai_ds_ag: variant.spliceai_ds_ag ?? null,
        spliceai_ds_al: variant.spliceai_ds_al ?? null,
        spliceai_ds_dg: variant.spliceai_ds_dg ?? null,
        spliceai_ds_dl: variant.spliceai_ds_dl ?? null,
        spliceai_dp_ag: variant.spliceai_dp_ag ?? null,
        spliceai_dp_al: variant.spliceai_dp_al ?? null,
        spliceai_dp_dg: variant.spliceai_dp_dg ?? null,
        spliceai_dp_dl: variant.spliceai_dp_dl ?? null,
      }) ?? NONE, spliceAiTone(variant.spliceai_max), SPLICEAI_THRESHOLDS),
      variant.sift
        ? predictorRow('SIFT', formatPredictionScore(variant.sift), siftTone(variant.sift), SEQR_THRESHOLDS)
        : null,
      variant.polyphen
        ? predictorRow('PolyPhen', formatPredictionScore(variant.polyphen), polyphenTone(variant.polyphen), SEQR_THRESHOLDS)
        : null,
    ]),
  };
};

const geneSection = (variant: AcmgModalVariant, gene: AcmgGeneContext | undefined): EvidenceSection => {
  // PP2 reads the gene profile's missense Z, then the VCF's own.
  const missenseZ = finite(variant.gene_missense_z) ?? finite(variant.annotation_extra?.gene_missense_z);
  const fromProfile = (value: string | null | undefined): string =>
    gene?.unavailable ? 'gene profile not loaded' : text(value);
  const inheritance = unique(gene?.modesOfInheritance ?? []);
  return {
    title: 'Gene',
    criteria: 'PVS1 · PP2 · BP1 · BS2 · PM3',
    rows: [
      row('Inheritance (GenCC)', fromProfile(inheritance.join('; '))),
      row('Haploinsufficiency (ClinGen)', fromProfile(gene?.clingenDosageHaploinsufficiency)),
      row('pLI', formatScore(variant.gene_pli, 3)),
      row('Missense Z', formatScore(missenseZ)),
    ],
  };
};

const clinvarSection = (variant: AcmgModalVariant): EvidenceSection => {
  const stars = formatClinvarStars(variant.clinvar_stars);
  const mito = variant.chr === 'MT' ? variant.mito : null;
  return {
    title: 'ClinVar',
    criteria: 'PP5 · BP6 · PS1 · PM5',
    rows: present([
      row(
        'Classification',
        variant.clinvar ? `${formatTokenLabel(variant.clinvar)}${stars ? ` ${stars}` : ''}` : NONE,
      ),
      optionalRow('Review status', variant.clinvar_review_status ? formatTokenLabel(variant.clinvar_review_status) : null),
      optionalRow('Conditions', variant.clinvar_conditions?.length ? variant.clinvar_conditions.join('; ') : null),
      mito ? row('MITOMAP / ClinVar (mtDNA)', text(mito.clinicalSignificance)) : null,
      mito?.disorders?.length ? row('Disorders', mito.disorders.join('; ')) : null,
    ]),
  };
};

// How the phenotype reads for the de novo points (PS2, PM6).
const DE_NOVO_TIER_SHORT: Record<AcmgDeNovoPhenotypeTier, string> = {
  highly_specific: 'highly specific for the gene',
  consistent: 'consistent, not highly specific',
  heterogeneous: 'general terms only',
  not_consistent: 'not shown consistent',
};

const phenotypeSection = (
  variant: AcmgModalVariant,
  labels: string[],
  unavailable: boolean,
  familyScore: FamilyGenePhenotypeScoreOut | undefined,
): EvidenceSection => {
  // The ranking's score where prioritisation scored the variant, else the family's score for the gene.
  const ranked = finite(variant.priority?.phenotype_score);
  const score = ranked ?? finite(familyScore?.score);
  const matches = ((ranked !== null ? variant.priority?.phenotype_matches : familyScore?.matched) ?? [])
    .map((term) => term.label || term.hpo_id)
    .filter(Boolean);
  const tier = deNovoPhenotypeTier({ phenotypeScore: score });
  return {
    title: 'Phenotype',
    criteria: 'PP4 · PS2 · PM6',
    rows: present([
      row(
        'Proband HPO terms',
        unavailable ? 'could not be loaded' : labels.length ? labels.join('; ') : 'none recorded',
      ),
      score !== null
        ? row(
            'Gene–phenotype match',
            `${formatScore(score)}${tier ? ` · ${DE_NOVO_TIER_SHORT[tier.tier]}` : ''}${matches.length ? ` (${matches.join('; ')})` : ''}`,
          )
        : null,
    ]),
  };
};

const PARENTAGE_LABELS: Record<AcmgParentageCheck['status'], string> = {
  confirmed: 'confirmed',
  refuted: 'not parent and child',
  unconfirmed: 'not confirmed',
};

// What the Sample QC says of the proband and one parent, for the Family section.
function parentageReading(family: AcmgFamilyContext, parentId: string | undefined): string {
  if (!parentId) return 'not named in the pedigree';
  if (family.parentageUnavailable) return 'Sample QC could not be loaded';
  if (!family.parentage) return 'Sample QC not loaded';
  const check = family.parentage[parentId];
  return check ? `${PARENTAGE_LABELS[check.status]} — ${check.message}` : 'no parent-child check in the Sample QC';
}

function EvidenceList({ section }: { section: EvidenceSection }) {
  return (
    <section className="acmg-evidence-section" aria-label={section.title}>
      <p className="acmg-evidence-title">
        {section.title}
        <span className="acmg-evidence-criteria">Informs {section.criteria}</span>
      </p>
      <dl className="acmg-evidence-list">
        {section.rows.map((entry) => (
          <div key={entry.label} className="acmg-evidence-row">
            <dt>{entry.label}</dt>
            <dd>
              {entry.tone ? (
                <PredictorMark reading={{ label: entry.label, value: entry.value, tone: entry.tone, basis: entry.basis }} />
              ) : null}
              {entry.value}
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}

// Each member's call for the variant: GT and depth, or for an mtDNA variant the heteroplasmy.
function FamilyCalls({ variant, family }: { variant: AcmgModalVariant; family: AcmgFamilyContext }) {
  const mitoCalls = variant.chr === 'MT' ? variant.mito?.calls : undefined;
  const modes = (variant.priority?.segregation_modes ?? []).map(
    (mode) => SEGREGATION_MODE_LABELS[mode] ?? formatTokenLabel(mode),
  );
  const mito = variant.chr === 'MT' ? variant.mito : null;
  // The Sample QC's parentage checks of the proband (PS2 or PM6), for a nuclear variant.
  const proband =
    family.members.find((member) => (member.role ?? '').toLowerCase() === 'proband') ??
    family.members.find((member) => member.affected);
  const parents = proband && !mito ? parentsOf(proband.sampleId, family.parentLinks) : null;
  const rows = mitoCalls?.length
    ? mitoCalls.map((call) => ({
        sample: call.sampleId,
        role: call.role ?? '',
        affected: call.affected,
        call:
          finite(call.alleleFraction) !== null
            ? `${((call.alleleFraction as number) * 100).toFixed(1)}%${call.zygosity ? ` ${formatTokenLabel(call.zygosity)}` : ''}`
            : formatTokenLabel(call.zygosity),
        depth: null as number | null | undefined,
      }))
    : family.members.length
      ? family.members.map((member) => ({
          sample: member.sampleId,
          role: member.role,
          affected: member.affected as boolean | undefined,
          call: member.gt || NONE,
          depth: member.dp,
        }))
      : variant.genotypes.map((call) => ({
          sample: call.sample,
          role: '',
          affected: undefined as boolean | undefined,
          call: call.gt || NONE,
          depth: call.dp,
        }));
  return (
    <section className="acmg-evidence-section" aria-label="Family">
      <p className="acmg-evidence-title">
        Family
        <span className="acmg-evidence-criteria">Informs PS2 · PM6 · PM3 · PP1 · BS4 · BP2</span>
      </p>
      {rows.length ? (
        <table className="acmg-evidence-table">
          <thead>
            <tr>
              <th scope="col">Sample</th>
              <th scope="col">Role</th>
              <th scope="col">Status</th>
              <th scope="col">{mitoCalls?.length ? 'Heteroplasmy' : 'GT'}</th>
              {mitoCalls?.length ? null : <th scope="col">DP</th>}
            </tr>
          </thead>
          <tbody>
            {rows.map((entry) => (
              <tr key={entry.sample}>
                <td>{entry.sample}</td>
                <td>{entry.role ? formatTokenLabel(entry.role) : NONE}</td>
                <td>{entry.affected === undefined ? NONE : entry.affected ? 'affected' : 'unaffected'}</td>
                <td className="table-mono">{entry.call}</td>
                {mitoCalls?.length ? null : <td>{finite(entry.depth) === null ? NONE : String(entry.depth)}</td>}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="acmg-evidence-empty">No calls for this variant.</p>
      )}
      <dl className="acmg-evidence-list">
        {modes.length ? (
          <div className="acmg-evidence-row">
            <dt>Segregation</dt>
            <dd>{modes.join('; ')}</dd>
          </div>
        ) : null}
        {parents ? (
          <>
            <div className="acmg-evidence-row">
              <dt>Paternity (Sample QC)</dt>
              <dd>{parentageReading(family, parents.father)}</dd>
            </div>
            <div className="acmg-evidence-row">
              <dt>Maternity (Sample QC)</dt>
              <dd>{parentageReading(family, parents.mother)}</dd>
            </div>
          </>
        ) : null}
        {mito?.maternalTransmission ? (
          <div className="acmg-evidence-row">
            <dt>Maternal transmission</dt>
            <dd>{formatTokenLabel(mito.maternalTransmission)}</dd>
          </div>
        ) : null}
        {mito?.probandHaplogroup ? (
          <div className="acmg-evidence-row">
            <dt>Proband haplogroup</dt>
            <dd>{mito.probandHaplogroup}</dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}

export default function AcmgEvidencePanel({
  variant,
  geneContext,
  familyContext,
  probandHpoLabels,
  probandHpoUnavailable,
  genePhenotypeScore,
}: AcmgEvidencePanelProps) {
  const sections = [
    consequenceSection(variant),
    populationSection(variant),
    inSilicoSection(variant),
    geneSection(variant, geneContext),
    clinvarSection(variant),
    phenotypeSection(variant, probandHpoLabels, probandHpoUnavailable, genePhenotypeScore),
  ];
  return (
    <details className="acmg-evidence" open>
      <summary className="acmg-evidence-summary">Evidence</summary>
      <div className="acmg-evidence-grid">
        {sections.map((section) => (
          <EvidenceList key={section.title} section={section} />
        ))}
        <FamilyCalls variant={variant} family={familyContext} />
      </div>
    </details>
  );
}
