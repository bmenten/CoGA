// The small-variant filter form's sections (#528): the shell each one shares, and one
// component per section. Each reads what it needs from the form through
// FilterSectionForm and computes its own summary count and quick filters.
import { useState } from 'react';
import type { CSSProperties, ChangeEvent, ReactNode, SyntheticEvent } from 'react';
import InfoIcon from '../../components/InfoIcon';
import { joinFilterValues, parseCommaSeparatedValues } from '../../lib/sampleFilterState';
import {
  COLLABORATION_QUICK_TAGS,
  HET_GT_GROUP,
  HOM_GT_GROUP,
  PATHOGENIC_TO_VUS_CLASSIFICATIONS,
  REF_GT_GROUP,
  REVIEW_CLASSIFICATION_OPTIONS,
  resolveCarrierScreeningCoupleMembers,
  type FamilyMember,
  type GenePanel,
  type LocationProblems,
  type SmallFilterState,
  type SmallVariantSampleFilter,
  type SmallVariantSearchState,
} from './smallVariantSearch';
import { GENOTYPE_GROUP_HINTS } from '../../lib/genotypes';
import { countNonEmpty, stopSummaryInteraction } from './variantFilterFormUtils';

const TYPE_OPTIONS = ['', 'SNV', 'INDEL', 'MNV'];
const REVIEW_QUICK_CLASSIFICATION_VALUES = PATHOGENIC_TO_VUS_CLASSIFICATIONS;
const REVIEW_QUICK_CLASSIFICATION_FILTER = REVIEW_QUICK_CLASSIFICATION_VALUES.join(', ');
const EXCLUDE_QUICK_CLINVAR_VALUES = ['Benign', 'Likely benign'] as const;
const EXCLUDE_QUICK_CLINVAR_FILTER = EXCLUDE_QUICK_CLINVAR_VALUES.join(', ');
const SIFT_OPTIONS = ['', 'deleterious', 'deleterious_low_confidence', 'tolerated'];
const POLYPHEN_OPTIONS = ['', 'probably_damaging', 'possibly_damaging', 'benign'];
const SIFT_VALUE_OPTIONS = SIFT_OPTIONS.filter((value) => value);
const POLYPHEN_VALUE_OPTIONS = POLYPHEN_OPTIONS.filter((value) => value);

/** A term the Annotations section offers, with a short form of its definition. */
type AnnotationOption = { value: string; label: string; hint: string };
type AnnotationGroup = { id: string; label: string; options: readonly AnnotationOption[] };

// The VEP consequence terms, grouped as seqr groups them: a row of the high- and
// moderate-impact groups, a row of the coding and other groups, and the regulatory groups
// in the last row. A group's own box ticks or clears all its terms. The hints are short
// forms of the Sequence Ontology definitions.
const CONSEQUENCE_GROUP_ROWS: readonly (readonly AnnotationGroup[])[] = [
  [
    {
      id: 'nonsense',
      label: 'Nonsense',
      options: [
        {
          value: 'stop_gained',
          label: 'Stop gained',
          hint: 'A base change that creates a premature stop codon, shortening the protein.',
        },
      ],
    },
    {
      id: 'essential_splice_site',
      label: 'Essential splice site',
      options: [
        {
          value: 'splice_donor_variant',
          label: 'Splice donor variant',
          hint: "Changes one of the two bases at the 5' end of an intron (the splice donor).",
        },
        {
          value: 'splice_acceptor_variant',
          label: 'Splice acceptor variant',
          hint: "Changes one of the two bases at the 3' end of an intron (the splice acceptor).",
        },
      ],
    },
    {
      id: 'frameshift',
      label: 'Frameshift',
      options: [
        {
          value: 'frameshift_variant',
          label: 'Frameshift',
          hint: 'Inserts or deletes a number of bases that is not a multiple of three, shifting the reading frame.',
        },
      ],
    },
    {
      id: 'missense',
      label: 'Missense',
      options: [
        {
          value: 'stop_lost',
          label: 'Stop lost',
          hint: 'Changes the stop codon, so the protein is elongated.',
        },
        {
          value: 'start_lost',
          label: 'Start lost',
          hint: 'Changes at least one base of the start codon.',
        },
        {
          value: 'protein_altering_variant',
          label: 'Protein altering',
          hint: 'Changes the encoded protein in a way no more specific term describes.',
        },
        {
          value: 'missense_variant',
          label: 'Missense',
          hint: 'Changes a codon so that it encodes another amino acid.',
        },
      ],
    },
    {
      id: 'in_frame',
      label: 'In frame',
      options: [
        {
          value: 'inframe_insertion',
          label: 'In-frame insertion',
          hint: 'Inserts a multiple of three bases into the coding sequence.',
        },
        {
          value: 'inframe_deletion',
          label: 'In-frame deletion',
          hint: 'Deletes a multiple of three bases from the coding sequence.',
        },
      ],
    },
  ],
  [
    {
      id: 'synonymous',
      label: 'Synonymous',
      options: [
        {
          value: 'synonymous_variant',
          label: 'Synonymous',
          hint: 'Changes a codon without changing the amino acid it encodes.',
        },
        {
          value: 'start_retained_variant',
          label: 'Start retained',
          hint: 'Changes the start codon, which still encodes a start.',
        },
        {
          value: 'stop_retained_variant',
          label: 'Stop retained',
          hint: 'Changes the stop codon, which still encodes a stop.',
        },
      ],
    },
    {
      id: 'extended_splice_site',
      label: 'Extended splice site',
      options: [
        {
          value: 'splice_donor_5th_base_variant',
          label: 'Splice donor 5th base',
          hint: 'Changes the fifth base of the intron after the splice donor.',
        },
        {
          value: 'splice_region_variant',
          label: 'Splice region',
          hint: 'Within 1-3 bases of the exon or 3-8 bases of the intron at a splice site.',
        },
        {
          value: 'splice_donor_region_variant',
          label: 'Splice donor region',
          hint: 'Between the third and the sixth base of the intron after the splice donor.',
        },
        {
          value: 'splice_polypyrimidine_tract_variant',
          label: 'Splice polypyrimidine tract',
          hint: 'In the polypyrimidine tract, 3 to 17 bases before the splice acceptor.',
        },
        {
          value: 'extended_intronic_splice_region_variant',
          label: 'Extended intronic splice region',
          hint: "Deeper in the intron near a splice site, as VEP's SpliceRegion plugin marks it in its extended mode.",
        },
      ],
    },
    {
      id: 'other',
      label: 'Other',
      options: [
        {
          value: 'transcript_ablation',
          label: 'Transcript ablation',
          hint: 'Deletes the whole transcript.',
        },
        {
          value: 'incomplete_terminal_codon_variant',
          label: 'Incomplete terminal codon variant',
          hint: 'Changes the last codon of a transcript whose end is incompletely annotated.',
        },
        {
          value: 'coding_sequence_variant',
          label: 'Coding sequence variant',
          hint: 'Changes the coding sequence in a way no more specific term describes.',
        },
        {
          value: 'mature_miRNA_variant',
          label: 'Mature miRNA variant',
          hint: 'Within the sequence of a mature microRNA.',
        },
        {
          value: '5_prime_UTR_variant',
          label: "5' UTR variant",
          hint: "In the 5' untranslated region.",
        },
        {
          value: '3_prime_UTR_variant',
          label: "3' UTR variant",
          hint: "In the 3' untranslated region.",
        },
        {
          value: 'intron_variant',
          label: 'Intron variant',
          hint: 'Within an intron.',
        },
        {
          value: 'upstream_gene_variant',
          label: 'Upstream gene variant',
          hint: "Upstream of a gene's transcript (within 5 kb, VEP's default).",
        },
        {
          value: 'downstream_gene_variant',
          label: 'Downstream gene variant',
          hint: "Downstream of a gene's transcript (within 5 kb, VEP's default).",
        },
        {
          value: 'NMD_transcript_variant',
          label: 'NMD transcript variant',
          hint: 'In a transcript that is a target of nonsense-mediated decay.',
        },
        {
          value: 'non_coding_transcript_exon_variant',
          label: 'Non-coding transcript exon variant',
          hint: 'In an exon of a non-coding transcript.',
        },
        {
          value: 'non_coding_transcript_variant',
          label: 'Non-coding transcript variant',
          hint: 'In a non-coding transcript.',
        },
        {
          value: 'coding_transcript_variant',
          label: 'Coding transcript variant',
          hint: 'In a transcript of a protein-coding gene.',
        },
        {
          value: 'intergenic_variant',
          label: 'Intergenic variant',
          hint: 'Between genes.',
        },
        {
          value: 'sequence_variant',
          label: 'Sequence variant',
          hint: 'A change VEP describes with no more specific term.',
        },
      ],
    },
  ],
];

const REGULATORY_CONSEQUENCE_GROUPS: readonly AnnotationGroup[] = [
  {
    id: 'motif_feature',
    label: 'Motif feature',
    options: [
      {
        value: 'TFBS_ablation',
        label: 'TFBS ablation',
        hint: 'Deletes a transcription factor binding site.',
      },
      {
        value: 'TFBS_amplification',
        label: 'TFBS amplification',
        hint: 'Amplifies a region that contains a transcription factor binding site.',
      },
      {
        value: 'TF_binding_site_variant',
        label: 'TF binding site variant',
        hint: 'Within a transcription factor binding site that Ensembl annotates.',
      },
    ],
  },
  {
    id: 'regulatory_feature',
    label: 'Regulatory feature',
    options: [
      {
        value: 'regulatory_region_variant',
        label: 'Regulatory region variant',
        hint: 'Within a regulatory region that Ensembl annotates.',
      },
      {
        value: 'regulatory_region_ablation',
        label: 'Regulatory region ablation',
        hint: 'Deletes a regulatory region.',
      },
      {
        value: 'regulatory_region_amplification',
        label: 'Regulatory region amplification',
        hint: 'Amplifies a region that contains a regulatory region.',
      },
    ],
  },
];

/** Every consequence group the Annotations section offers, in its order; the variant summary
 *  groups its consequence counts by them. */
export const CONSEQUENCE_GROUPS: readonly AnnotationGroup[] = [
  ...CONSEQUENCE_GROUP_ROWS.flat(),
  ...REGULATORY_CONSEQUENCE_GROUPS,
];

const OFFERED_CONSEQUENCES = new Set(
  CONSEQUENCE_GROUPS.flatMap((group) => group.options.map((option) => option.value.toLowerCase())),
);

// VEP's own impact rating of a transcript's consequence. The built-in presets filter on it.
const VEP_IMPACT_OPTIONS: readonly AnnotationOption[] = [
  {
    value: 'HIGH',
    label: 'HIGH',
    hint: 'Assumed to disrupt the protein: stop gained, frameshift, essential splice site, start or stop lost.',
  },
  {
    value: 'MODERATE',
    label: 'MODERATE',
    hint: 'May change the protein without disrupting it: missense, in-frame insertion or deletion.',
  },
  {
    value: 'LOW',
    label: 'LOW',
    hint: 'Assumed mostly harmless: synonymous, splice region.',
  },
  {
    value: 'MODIFIER',
    label: 'MODIFIER',
    hint: 'Non-coding, or no evidence of an impact: intronic, UTR, up- or downstream, intergenic.',
  },
];

/** Whether a list filter holds the value, as the search reads it: case aside. */
const holdsValue = (selected: readonly string[], value: string) =>
  selected.some((entry) => entry.toLowerCase() === value.toLowerCase());

/** A list filter with the values added, or with them removed when `checked` is false. */
const withValues = (selected: readonly string[], values: readonly string[], checked: boolean) => {
  const folded = new Set(values.map((value) => value.toLowerCase()));
  const kept = selected.filter((entry) => !folded.has(entry.toLowerCase()));
  return joinFilterValues(checked ? [...kept, ...values] : kept);
};

const countTextAreaEntries = (value: string) =>
  value
    .split(/\n|,|;/)
    .map((entry) => entry.trim())
    .filter(Boolean).length;

const CODING_CONSEQUENCE_SET = new Set<string>([
  'missense_variant',
  'frameshift_variant',
  'stop_gained',
  'stop_lost',
  'start_lost',
  'splice_acceptor_variant',
  'splice_donor_variant',
  'splice_region_variant',
  'inframe_insertion',
  'inframe_deletion',
  'protein_altering_variant',
  'synonymous_variant',
  'coding_sequence_variant',
]);

// The genotype groups the per-sample toggles and the inheritance quick filters set: the
// same lists the search state toggles (handleGtToggle), so the three always agree.
export const GT_GROUP_OPTIONS = [
  { id: 'hom-group', values: HOM_GT_GROUP },
  { id: 'het-group', values: HET_GT_GROUP },
  { id: 'ref-group', values: REF_GT_GROUP },
] as const;

export type GtGroupId = (typeof GT_GROUP_OPTIONS)[number]['id'];

/**
 * One of the form's collapsible filter sections (#528): its title, a one-line summary of
 * what is set, any quick controls in the summary bar, and the section's own fields.
 */
const FilterSection = ({
  title,
  meta,
  open,
  onToggle,
  controls,
  children,
}: {
  title: string;
  meta: ReactNode;
  open: boolean;
  onToggle: (event: SyntheticEvent<HTMLDetailsElement>) => void;
  controls?: ReactNode;
  children: ReactNode;
}) => (
  <details className="variant-filter-dropdown" open={open} onToggle={onToggle}>
    <summary className="variant-filter-dropdown-summary">
      <span className="variant-filter-dropdown-summary-copy">
        <span className="variant-filter-dropdown-title">{title}</span>
        <span className="variant-filter-dropdown-meta">{meta}</span>
      </span>
      {controls !== undefined ? (
        <span
          className="variant-filter-dropdown-summary-controls"
          role="presentation"
          onMouseDown={stopSummaryInteraction}
          onClick={stopSummaryInteraction}
        >
          {controls}
        </span>
      ) : null}
      <span className="variant-filter-dropdown-caret" aria-hidden="true">
        ▾
      </span>
    </summary>
    {children}
  </details>
);

/** One position of a step slider; '' is the step that sets no limit. */
type SliderStep = { value: string; label: string };

const AF_SLIDER_STEPS: readonly SliderStep[] = [
  ...['0', '0.0001', '0.0005', '0.001', '0.005', '0.01', '0.02', '0.03', '0.04', '0.05', '0.1'].map(
    (value) => ({ value, label: value }),
  ),
  { value: '', label: '1' },
];

// A minimum of 0 sets no limit, so the first step of a quality slider clears the field.
const countSteps = (max: number, every = 1): SliderStep[] =>
  Array.from({ length: Math.floor(max / every) + 1 }, (_, index) => {
    const count = index * every;
    return { value: count === 0 ? '' : String(count), label: String(count) };
  });

const GQ_SLIDER_STEPS = countSteps(100, 5);
const DP_SLIDER_STEPS = countSteps(50);
const AD_SLIDER_STEPS = countSteps(20);
// The allele fraction is kept as a fraction (0.2) and shown as a percent (20%).
const AB_SLIDER_STEPS: readonly SliderStep[] = Array.from({ length: 101 }, (_, percent) => ({
  value: percent === 0 ? '' : String(percent / 100),
  label: `${percent}%`,
}));

/** The step a value sits on: its own step, else the nearest one. */
const sliderStepIndex = (steps: readonly SliderStep[], value: string) => {
  const trimmed = value.trim();
  if (!trimmed) return Math.max(0, steps.findIndex((step) => step.value === ''));
  const target = Number(trimmed);
  let nearest = 0;
  let nearestDistance = Number.POSITIVE_INFINITY;
  steps.forEach((step, index) => {
    if (step.value === '') return;
    const distance = Math.abs(Number(step.value) - target);
    if (distance < nearestDistance) {
      nearest = index;
      nearestDistance = distance;
    }
  });
  return nearest;
};

/**
 * A slider over fixed steps, as seqr's: the value it holds above the thumb, the first and
 * last steps under the track. A value between two steps (from a saved search or a link) is
 * shown as it is, with the thumb at the nearest step, and changes only when the thumb is
 * moved. `value` null reads "mixed": the members it sets differ.
 */
const StepSlider = ({
  name,
  steps,
  value,
  onChange,
}: {
  name: string;
  steps: readonly SliderStep[];
  value: string | null;
  onChange: (value: string) => void;
}) => {
  const index = value === null ? 0 : sliderStepIndex(steps, value);
  const step = steps[index];
  const onStep =
    value !== null &&
    (value.trim() === '' ? step.value === '' : step.value !== '' && Number(step.value) === Number(value));
  const shown = value === null ? 'mixed' : onStep ? step.label : value.trim();
  const position = steps.length > 1 ? index / (steps.length - 1) : 0;
  return (
    <div
      className="variant-step-slider"
      style={{ '--slider-position': String(position) } as CSSProperties}
    >
      <span className="variant-step-slider-value" aria-hidden="true">
        {shown}
      </span>
      <input
        type="range"
        min={0}
        max={steps.length - 1}
        step={1}
        value={index}
        aria-label={name}
        aria-valuetext={shown}
        onChange={(event) => onChange(steps[Number(event.target.value)].value)}
      />
      <span className="variant-step-slider-ends" aria-hidden="true">
        <span>{steps[0].label}</span>
        <span>{steps[steps.length - 1].label}</span>
      </span>
    </div>
  );
};

/** A column title with the "i" that explains it. */
const ColumnTitle = ({ label, hint }: { label: string; hint: string }) => (
  <span className="variant-filter-column-title">
    {label}
    <InfoIcon name={`About ${label}`} text={hint} />
  </span>
);

/** A ticked-or-not term of the Annotations section, with the "i" that explains it. */
const AnnotationCheckbox = ({
  label,
  hint,
  checked,
  onChange,
}: {
  label: string;
  hint?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) => (
  <span className="variant-annotation-option">
    <label className="analysis-checkbox variant-compact-checkbox">
      <input
        type="checkbox"
        checked={checked}
        onChange={(event) => onChange(event.target.checked)}
      />
      {label}
    </label>
    {hint ? <InfoIcon name={`About ${label}`} text={hint} /> : null}
  </span>
);

/**
 * A column of the Annotations section: the group's terms, under a box that ticks or clears
 * them all (half-ticked when some are) or, for a group of choices that are not one kind of
 * consequence (the VEP impacts), under its title alone.
 */
const AnnotationGroupColumn = ({
  group,
  selected,
  onChange,
  wholeGroupBox = true,
  className,
}: {
  group: AnnotationGroup;
  selected: readonly string[];
  onChange: (values: readonly string[], checked: boolean) => void;
  wholeGroupBox?: boolean;
  className?: string;
}) => {
  const values = group.options.map((option) => option.value);
  const held = values.filter((value) => holdsValue(selected, value)).length;
  return (
    <div
      className={`variant-annotation-group${className ? ` ${className}` : ''}`}
      role="group"
      aria-label={group.label}
    >
      {wholeGroupBox ? (
        <label className="analysis-checkbox variant-annotation-group-title">
          <input
            type="checkbox"
            aria-label={`${group.label} (whole group)`}
            checked={held === values.length}
            ref={(input) => {
              if (input) input.indeterminate = held > 0 && held < values.length;
            }}
            onChange={(event) => onChange(values, event.target.checked)}
          />
          {group.label}
        </label>
      ) : (
        <span className="variant-annotation-group-title">{group.label}</span>
      )}
      <div className="variant-annotation-options">
        {group.options.map((option) => (
          <AnnotationCheckbox
            key={option.value}
            label={option.label}
            hint={option.hint}
            checked={holdsValue(selected, option.value)}
            onChange={(checked) => onChange([option.value], checked)}
          />
        ))}
      </div>
    </div>
  );
};

export const DEFAULT_OPEN_SECTIONS = {
  phenotype: false,
  svSecondHit: false,
  inheritance: false,
  categories: false,
  pathogenicity: false,
  annotations: false,
  inSilico: false,
  frequency: false,
  callQuality: false,
  locations: false,
  exclude: false,
  review: false,
};
export type OpenSections = typeof DEFAULT_OPEN_SECTIONS;
type FilterOption = { value: string; label: string };

/** What the section components read from the form (#528). */
export interface FilterSectionForm
  extends Pick<
    SmallVariantSearchState,
    | 'draftFilters'
    | 'members'
    | 'sampleDraftFilters'
    | 'handleGtToggle'
    | 'handleSampleFieldChange'
    | 'setDraftFilterValue'
    | 'toggleDraftFilterListValue'
  > {
  panels: GenePanel[];
  categoryLabels?: Record<number, string>;
  niptInheritancePresets?: { value: string; label: string; categories?: string }[];
  categoryCounts: Record<string, number> | null;
  openSections: OpenSections;
  handleSectionToggle: (
    section: keyof OpenSections,
  ) => (event: SyntheticEvent<HTMLDetailsElement>) => void;
  summarizeSection: (count: number, emptyLabel: string) => string;
  offers: (key: keyof SmallFilterState) => boolean;
  handleDraftFieldChange: (
    event: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => void;
  toggleSingleValueCheckbox: (key: keyof SmallFilterState, optionValue: string) => void;
  updateInheritanceGenotypes: (strategy: (member: FamilyMember) => ReadonlySet<GtGroupId>) => void;
  /** The members whose genotypes are narrowed (some, not all, genotype groups ticked). */
  activeGenotypeMemberCount: number;
  /** The members with a call-quality minimum set. */
  activeQualityMemberCount: number;
  carrierScreeningCouple: ReturnType<typeof resolveCarrierScreeningCoupleMembers>;
  clinvarOptions: FilterOption[];
  standardTagOptions: FilterOption[];
  customTagOptions: FilterOption[];
  /** The selected tags no option offers (a deleted tag), to be shown so they can be unticked. */
  selectedHeldTagOptions: (selected: string[]) => FilterOption[];
  /** Draft location filters that cannot be read, shown under their fields (#604). */
  draftLocationProblems?: LocationProblems | null;
}

/**
 * How many filters each section has set in the draft: the count its summary bar shows
 * ("2 active"), and what the collapsed advanced panel adds up.
 */
export const SECTION_FILTER_COUNTS: Record<
  keyof OpenSections,
  (
    form: Pick<
      FilterSectionForm,
      'draftFilters' | 'activeGenotypeMemberCount' | 'activeQualityMemberCount' | 'offers'
    >,
  ) => number
> = {
  phenotype: ({ draftFilters }) => (draftFilters.prioritize === 'true' ? 1 : 0),
  svSecondHit: ({ draftFilters }) => (draftFilters.require_sv_second_hit === 'true' ? 1 : 0),
  inheritance: ({ draftFilters, activeGenotypeMemberCount }) =>
    activeGenotypeMemberCount +
    (draftFilters.expanded_carrier_screening === 'true' ? 1 : 0) +
    countNonEmpty(draftFilters.inheritance, draftFilters.type, draftFilters.source, draftFilters.ps),
  categories: ({ draftFilters }) => parseCommaSeparatedValues(draftFilters.category).length,
  pathogenicity: ({ draftFilters }) => parseCommaSeparatedValues(draftFilters.clinvar).length,
  // SpliceAI is set under Annotations, as in seqr; the search also reads it as an in-silico
  // predictor (the In silico note says so).
  annotations: ({ draftFilters }) =>
    parseCommaSeparatedValues(draftFilters.impact).length +
    parseCommaSeparatedValues(draftFilters.effect).length +
    countNonEmpty(
      draftFilters.min_spliceai,
      draftFilters.transcript,
      draftFilters.rsid,
      draftFilters.hgvsc,
      draftFilters.hgvsp,
      draftFilters.canonical_only,
      draftFilters.mane_only,
      draftFilters.lof_only,
    ),
  inSilico: ({ draftFilters }) =>
    countNonEmpty(
      draftFilters.min_cadd,
      draftFilters.min_revel,
      draftFilters.sift,
      draftFilters.polyphen,
    ),
  frequency: ({ draftFilters }) =>
    countNonEmpty(
      draftFilters.max_gnomad_af,
      draftFilters.max_gnomad_exomes_af,
      draftFilters.max_gnomad_genomes_af,
      draftFilters.max_gnomad_popmax_af,
      draftFilters.max_topmed_af,
      draftFilters.max_gnomad_ac,
      draftFilters.max_gnomad_hom_count,
      draftFilters.max_gnomad_hemi_count,
    ),
  callQuality: ({ draftFilters, activeQualityMemberCount }) =>
    activeQualityMemberCount + (draftFilters.pass_only === 'true' ? 1 : 0),
  locations: ({ draftFilters }) =>
    countNonEmpty(draftFilters.panel_id) +
    (draftFilters.gene.trim() ? countTextAreaEntries(draftFilters.gene) : 0) +
    (draftFilters.intervals.trim() ? countTextAreaEntries(draftFilters.intervals) : 0),
  exclude: ({ draftFilters }) =>
    parseCommaSeparatedValues(draftFilters.exclude_clinvar).length +
    parseCommaSeparatedValues(draftFilters.exclude_review_tags).length +
    (draftFilters.exclude_gene.trim() ? countTextAreaEntries(draftFilters.exclude_gene) : 0) +
    (draftFilters.exclude_intervals.trim()
      ? countTextAreaEntries(draftFilters.exclude_intervals)
      : 0),
  // A search without the classification filter (the NIPT list) offers the review tags only.
  review: ({ draftFilters, offers }) =>
    (offers('classification') ? parseCommaSeparatedValues(draftFilters.classification).length : 0) +
    parseCommaSeparatedValues(draftFilters.review_tags).length +
    (offers('has_notes') && draftFilters.has_notes === 'true' ? 1 : 0),
};

/** The unreadable entries of a location field, named so the search is not run on them. */
const LocationProblemList = ({ problems }: { problems: string[] }) =>
  problems.length ? (
    <div className="variant-workspace-feedback variant-workspace-feedback--error" role="alert">
      {problems.join(' ')} Nothing was searched.
    </div>
  ) : null;

export const PhenotypeFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleSectionToggle,
    openSections,
    setDraftFilterValue,
    summarizeSection,
  } = form;
  return (
    <FilterSection
      title="Phenotype"
      open={openSections.phenotype}
      onToggle={handleSectionToggle('phenotype')}
      meta={
summarizeSection(SECTION_FILTER_COUNTS.phenotype(form), 'Prioritization off')
      }
      controls={
        <>
          <label className="analysis-checkbox">
            <input
              type="checkbox"
              checked={draftFilters.prioritize === 'true'}
              onChange={(event) =>
                setDraftFilterValue('prioritize', event.target.checked ? 'true' : '')
              }
            />
            <span>Phenotype prioritization</span>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="table-subtle">
          Rank candidate variants by how well each gene matches the affected
          individuals’ HPO phenotypes (Monarch / Exomiser-style).
        </p>
      </div>
    </FilterSection>
  );
};

export const SvSecondHitFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleSectionToggle,
    openSections,
    setDraftFilterValue,
    summarizeSection,
  } = form;
  return (
    <FilterSection
      title="Structural second hit"
      open={openSections.svSecondHit}
      onToggle={handleSectionToggle('svSecondHit')}
      meta={
summarizeSection(SECTION_FILTER_COUNTS.svSecondHit(form), 'Off')
      }
      controls={
        <>
          <label className="analysis-checkbox">
            <input
              type="checkbox"
              checked={draftFilters.require_sv_second_hit === 'true'}
              onChange={(event) =>
                setDraftFilterValue(
                  'require_sv_second_hit',
                  event.target.checked ? 'true' : '',
                )
              }
            />
            <span>Also hit by an SV</span>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="table-subtle">
          Restrict to variants whose gene is also hit by a structural variant — the
          cross-type “second hit” (e.g. an SNV plus an overlapping deletion). Matches are
          tagged with the SV type and a trans/cis phase badge.
        </p>
      </div>
    </FilterSection>
  );
};

export const InheritanceFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    carrierScreeningCouple,
    draftFilters,
    handleDraftFieldChange,
    handleGtToggle,
    handleSectionToggle,
    members,
    openSections,
    sampleDraftFilters,
    setDraftFilterValue,
    summarizeSection,
    toggleSingleValueCheckbox,
    updateInheritanceGenotypes,
  } = form;
  const inheritanceFilterCount = SECTION_FILTER_COUNTS.inheritance(form);

  const applyInheritanceQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('inheritance', '');
      setDraftFilterValue('expanded_carrier_screening', '');
      updateInheritanceGenotypes(() => new Set<GtGroupId>(['hom-group', 'het-group', 'ref-group']));
      return;
    }

    if (value === 'de_novo_dominant') {
      setDraftFilterValue('inheritance', 'de_novo_dominant');
      setDraftFilterValue('expanded_carrier_screening', '');
      updateInheritanceGenotypes((member) =>
        member.affected ? new Set<GtGroupId>(['het-group']) : new Set<GtGroupId>(['ref-group']),
      );
      return;
    }

    if (value === 'recessive_homozygous') {
      setDraftFilterValue('inheritance', 'recessive_homozygous');
      setDraftFilterValue('expanded_carrier_screening', '');
      updateInheritanceGenotypes((member) => {
        if (member.affected) return new Set<GtGroupId>(['hom-group']);
        if (member.role === 'mother' || member.role === 'father') {
          return new Set<GtGroupId>(['het-group']);
        }
        return new Set<GtGroupId>(['ref-group', 'het-group']);
      });
      return;
    }

    if (value === 'compound_heterozygous' || value === 'compound_het') {
      setDraftFilterValue('inheritance', 'compound_het');
      setDraftFilterValue('expanded_carrier_screening', '');
      updateInheritanceGenotypes((member) =>
        member.affected
          ? new Set<GtGroupId>(['het-group'])
          : new Set<GtGroupId>(['ref-group', 'het-group']),
      );
      return;
    }

    if (value === 'x_linked') {
      setDraftFilterValue('inheritance', 'x_linked');
      setDraftFilterValue('expanded_carrier_screening', '');
      updateInheritanceGenotypes((member) => {
        if (member.affected && member.sex === 'male') return new Set<GtGroupId>(['hom-group']);
        if (member.affected) return new Set<GtGroupId>(['hom-group', 'het-group']);
        if (member.role === 'mother') return new Set<GtGroupId>(['ref-group', 'het-group']);
        return new Set<GtGroupId>(['ref-group']);
      });
    }
  };

  const selectedInheritanceQuickFilter = (() => {
    if (!draftFilters.inheritance) {
      return 'all';
    }
    if (draftFilters.inheritance === 'compound_het') {
      return 'compound_het';
    }
    if (draftFilters.inheritance === 'de_novo_dominant') {
      return 'de_novo_dominant';
    }
    if (draftFilters.inheritance === 'recessive_homozygous') {
      return 'recessive_homozygous';
    }
    if (draftFilters.inheritance === 'x_linked') {
      return 'x_linked';
    }
    return 'custom';
  })();
  return (
    <FilterSection
      title="Inheritance"
      open={openSections.inheritance}
      onToggle={handleSectionToggle('inheritance')}
      meta={summarizeSection(inheritanceFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Inheritance model</span>
            <select
              aria-label="Quick inheritance"
              value={selectedInheritanceQuickFilter}
              onChange={(event) => applyInheritanceQuickFilter(event.target.value)}
            >
              <option value="all">All</option>
              <option value="de_novo_dominant">De novo/dominant</option>
              <option value="recessive_homozygous">Recessive homozygous</option>
              <option value="compound_het">Compound heterozygous</option>
              <option value="x_linked">X-linked</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        {carrierScreeningCouple ? (
          <label className="analysis-checkbox">
            <input
              type="checkbox"
              checked={draftFilters.expanded_carrier_screening === 'true'}
              onChange={(event) =>
                setDraftFilterValue(
                  'expanded_carrier_screening',
                  event.target.checked ? 'true' : '',
                )
              }
            />
            Couple-based expanded carrier screening
          </label>
        ) : null}
        {carrierScreeningCouple ? (
          <p className="table-subtle">
            Restricts results to genes where both {carrierScreeningCouple.left.sample_id} and{' '}
            {carrierScreeningCouple.right.sample_id} carry a variant, and to variants a female partner
            carries on chrX outside the pseudo-autosomal regions: for an X-linked condition her carrier
            status alone puts a son at risk. A partner whose sex is not recorded counts as female.
          </p>
        ) : null}

        <div className="variant-sample-grid">
          {members.map((member) => {
            const sample = member.sample_id;
            const filter = sampleDraftFilters[sample];
            const sexSymbol =
              member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚧';
            return (
              <div key={sample} className="variant-sample-row">
                <div className="variant-sample-heading">
                  <span className="variant-sample-title">
                    {sexSymbol} {sample}
                  </span>
                  <div className="variant-sample-meta">
                    <span className="table-chip">{member.role}</span>
                    <span
                      className={`table-chip ${member.affected ? 'badge-chip--signature' : ''}`}
                    >
                      {member.affected ? 'affected' : 'unaffected'}
                    </span>
                  </div>
                </div>
                <div className="variant-sample-controls">
                  <div className="variant-gt-toggle-row">
                    {[
                      { value: 'hom-group', label: 'Hom', group: HOM_GT_GROUP },
                      { value: 'het-group', label: 'Het', group: HET_GT_GROUP },
                      { value: 'ref-group', label: 'WT', group: REF_GT_GROUP },
                    ].map((option) => (
                      <label
                        key={option.value}
                        className="analysis-checkbox"
                        title={GENOTYPE_GROUP_HINTS[option.value as keyof typeof GENOTYPE_GROUP_HINTS]}
                      >
                        <input
                          type="checkbox"
                          checked={option.group.every((gt) => filter?.gt.includes(gt))}
                          onChange={(event) =>
                            handleGtToggle(sample, option.value, event.target.checked)
                          }
                        />
                        {option.label}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
            );
          })}
        </div>

        <div className="analysis-filter-grid analysis-filter-grid--3">
          <div>
            <p className="variant-annotation-impact-title">Any variant type</p>
            <div className="variant-checkbox-grid variant-checkbox-grid--compact">
            {TYPE_OPTIONS.filter((option) => option).map((option) => (
              <label key={option} className="analysis-checkbox variant-compact-checkbox">
                <input
                  type="checkbox"
                  checked={draftFilters.type === option}
                  onChange={() => toggleSingleValueCheckbox('type', option)}
                />
                {option}
              </label>
            ))}
            </div>
          </div>
          <input
            name="source"
            placeholder="Callset / source"
            value={draftFilters.source}
            onChange={handleDraftFieldChange}
          />
          <input
            name="ps"
            placeholder="Phase set"
            value={draftFilters.ps}
            onChange={handleDraftFieldChange}
          />
        </div>
      </div>
    </FilterSection>
  );
};

// What each NIPT inheritance view lists (nipt_service.get_family_nipt_variants).
const NIPT_VIEW_NOTES: Record<string, string> = {
  de_novo:
    "Calls in the fetal window of the paternal alleles' allele fractions, without a supported call in the father, triaged and ranked by score. Confirm a candidate's absence in the father and its allele fraction before acting on it.",
  paternal_dominant:
    "The father's alleles the mother does not carry, and whether the fetus inherited each (seen at FF/2 in the plasma, or absent where it would have shown).",
  maternal_dominant:
    "The mother's alleles, and the probability that the fetus inherited each (the plasma's allele fraction against 50% - FF/2, 50% and 50% + FF/2).",
  recessive_at_risk:
    'The genes where both parents carry an allele, with the probability that the fetus inherited each and both.',
};

export const NiptCategoriesFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    categoryCounts,
    categoryLabels,
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    niptInheritancePresets,
    openSections,
    setDraftFilterValue,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  return (
    <FilterSection
      title="Maternal/fetal categories"
      open={openSections.categories}
      onToggle={handleSectionToggle('categories')}
      meta={
summarizeSection(SECTION_FILTER_COUNTS.categories(form), 'All categories')
      }
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Inheritance</span>
            <select
              aria-label="NIPT inheritance preset"
              value={draftFilters.inheritance}
              onChange={(event) => {
                const value = event.target.value;
                setDraftFilterValue('inheritance', value);
                // Picking an inheritance preset also ticks its category
                // checkboxes (de novo → 1, paternal dominant → 7, …).
                const selected = (niptInheritancePresets ?? []).find(
                  (preset) => preset.value === value,
                );
                if (selected && selected.categories !== undefined) {
                  setDraftFilterValue('category', selected.categories);
                }
              }}
            >
              {(niptInheritancePresets ?? []).map((preset) => (
                <option key={preset.value} value={preset.value}>
                  {preset.label}
                </option>
              ))}
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        {NIPT_VIEW_NOTES[draftFilters.inheritance] ? (
          <p className="table-subtle">{NIPT_VIEW_NOTES[draftFilters.inheritance]}</p>
        ) : null}
        {draftFilters.inheritance === 'de_novo' ? (
          <label className="analysis-field-label">
            List de novo candidates of priority
            <select
              aria-label="Lowest de novo priority"
              value={draftFilters.de_novo_priority || 'low'}
              onChange={(event) =>
                setDraftFilterValue('de_novo_priority', event.target.value === 'low' ? '' : event.target.value)
              }
            >
              <option value="high">High only</option>
              <option value="medium">High and medium</option>
              <option value="low">Every candidate in the fetal window</option>
            </select>
          </label>
        ) : null}
        {draftFilters.inheritance === 'paternal_dominant' || draftFilters.inheritance === 'maternal_dominant' ? (
          <label className="analysis-checkbox">
            <input
              type="checkbox"
              checked={draftFilters.include_not_inherited === 'true'}
              onChange={(event) => setDraftFilterValue('include_not_inherited', event.target.checked ? 'true' : '')}
            />
            <span>Also list the alleles the fetus did not inherit</span>
          </label>
        ) : null}
        <div className="nipt-category-grid">
          {[1, 2, 3, 4, 5, 6, 7, 8].map((categoryNumber) => {
            const value = String(categoryNumber);
            const selected = parseCommaSeparatedValues(draftFilters.category).includes(value);
            return (
              <label key={categoryNumber} className="analysis-checkbox nipt-category-option">
                <input
                  type="checkbox"
                  checked={selected}
                  onChange={() => toggleDraftFilterListValue('category', value)}
                />
                <span className="nipt-category-option-copy">
                  <span className="nipt-category-option-label">
                    {categoryNumber} —{' '}
                    {categoryLabels?.[categoryNumber] ?? `Category ${categoryNumber}`}
                  </span>
                  <span className="nipt-category-option-count">
                    {categoryCounts ? (categoryCounts[value] ?? 0).toLocaleString() : '—'}
                  </span>
                </span>
              </label>
            );
          })}
        </div>
        <label className="analysis-field-label">
          Min classification confidence
          <input
            type="number"
            name="min_confidence"
            min={0}
            max={1}
            step={0.05}
            value={draftFilters.min_confidence}
            onChange={handleDraftFieldChange}
            placeholder="0.00"
          />
        </label>
      </div>
    </FilterSection>
  );
};

export const PathogenicityFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    clinvarOptions,
    draftFilters,
    handleSectionToggle,
    openSections,
    setDraftFilterValue,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  const pathogenicityFilterCount = SECTION_FILTER_COUNTS.pathogenicity(form);
  const selectedClinvarValues = parseCommaSeparatedValues(draftFilters.clinvar);
  const applyPathogenicityQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('clinvar', '');
      return;
    }
    if (value === 'path_likely_path') {
      setDraftFilterValue('clinvar', 'Pathogenic, Likely pathogenic');
      return;
    }
    if (value === 'not_benign') {
      setDraftFilterValue(
        'clinvar',
        'Pathogenic, Likely pathogenic, Uncertain significance, Conflicting classifications',
      );
    }
  };

  const selectedPathogenicityQuickFilter = (() => {
    const selected = new Set(parseCommaSeparatedValues(draftFilters.clinvar));
    if (selected.size === 0) return 'all';
    if (selected.size === 2 && selected.has('Pathogenic') && selected.has('Likely pathogenic')) {
      return 'path_likely_path';
    }
    if (
      selected.size === 4 &&
      selected.has('Pathogenic') &&
      selected.has('Likely pathogenic') &&
      selected.has('Uncertain significance') &&
      selected.has('Conflicting classifications')
    ) {
      return 'not_benign';
    }
    return 'custom';
  })();
  return (
    <FilterSection
      title="Pathogenicity"
      open={openSections.pathogenicity}
      onToggle={handleSectionToggle('pathogenicity')}
      meta={summarizeSection(pathogenicityFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Quick</span>
            <select
              aria-label="Quick pathogenicity"
              value={selectedPathogenicityQuickFilter}
              onChange={(event) => applyPathogenicityQuickFilter(event.target.value)}
            >
              <option value="all">All</option>
              <option value="path_likely_path">P/LP</option>
              <option value="not_benign">Not benign</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="variant-annotation-impact-title">ClinVar status</p>
        <div className="variant-checkbox-grid variant-checkbox-grid--small">
          {clinvarOptions.map((option) => (
            <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
              <input
                type="checkbox"
                checked={selectedClinvarValues.includes(option.value)}
                onChange={() => toggleDraftFilterListValue('clinvar', option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
      </div>
    </FilterSection>
  );
};

export const AnnotationsFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    offers,
    openSections,
    setDraftFilterValue,
    summarizeSection,
  } = form;
  const annotationFilterCount = SECTION_FILTER_COUNTS.annotations(form);
  const selectedImpactValues = parseCommaSeparatedValues(draftFilters.impact);
  const selectedEffectValues = parseCommaSeparatedValues(draftFilters.effect);
  const applyAnnotationQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('impact', '');
      setDraftFilterValue('effect', '');
      return;
    }
    if (value === 'high_impact') {
      setDraftFilterValue('impact', 'HIGH');
      setDraftFilterValue('effect', '');
      return;
    }
    if (value === 'moderate_to_high') {
      setDraftFilterValue('impact', 'HIGH, MODERATE');
      setDraftFilterValue('effect', '');
      return;
    }
    if (value === 'all_coding') {
      setDraftFilterValue('impact', '');
      setDraftFilterValue('effect', Array.from(CODING_CONSEQUENCE_SET).join(', '));
    }
  };

  const setEffects = (values: readonly string[], checked: boolean) =>
    setDraftFilterValue('effect', withValues(selectedEffectValues, values, checked));
  const setImpacts = (values: readonly string[], checked: boolean) =>
    setDraftFilterValue('impact', withValues(selectedImpactValues, values, checked));
  // A term or impact a saved search or a link holds that no box offers is listed, ticked,
  // so it can be seen and unticked.
  const heldEffectGroup: AnnotationGroup = {
    id: 'held',
    label: 'Other selected terms',
    options: selectedEffectValues
      .filter((value) => !OFFERED_CONSEQUENCES.has(value.toLowerCase()))
      .map((value) => ({ value, label: value, hint: '' })),
  };
  const impactGroup: AnnotationGroup = {
    id: 'impact',
    label: 'VEP impact',
    options: [
      ...VEP_IMPACT_OPTIONS,
      ...selectedImpactValues
        .filter((value) => !VEP_IMPACT_OPTIONS.some((option) => holdsValue([option.value], value)))
        .map((value) => ({ value, label: value, hint: '' })),
    ],
  };

  const selectedAnnotationQuickFilter = (() => {
    const impactValues = parseCommaSeparatedValues(draftFilters.impact);
    const effectValues = parseCommaSeparatedValues(draftFilters.effect);
    const effectSet = new Set(effectValues);

    if (impactValues.length === 0 && effectValues.length === 0) return 'all';
    if (impactValues.length === 1 && impactValues[0] === 'HIGH' && effectValues.length === 0) {
      return 'high_impact';
    }
    if (
      impactValues.length === 2 &&
      impactValues.includes('HIGH') &&
      impactValues.includes('MODERATE') &&
      effectValues.length === 0
    ) {
      return 'moderate_to_high';
    }
    if (
      impactValues.length === 0 &&
      effectValues.length === CODING_CONSEQUENCE_SET.size &&
      Array.from(CODING_CONSEQUENCE_SET).every((term) => effectSet.has(term))
    ) {
      return 'all_coding';
    }
    return 'custom';
  })();
  return (
    <FilterSection
      title="Annotations"
      open={openSections.annotations}
      onToggle={handleSectionToggle('annotations')}
      meta={summarizeSection(annotationFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Quick</span>
            <select
              aria-label="Quick annotations"
              value={selectedAnnotationQuickFilter}
              onChange={(event) => applyAnnotationQuickFilter(event.target.value)}
            >
              <option value="all">All</option>
              <option value="high_impact">High impact</option>
              <option value="moderate_to_high">Moderate to high impact</option>
              <option value="all_coding">All coding</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="variant-filter-section-note">
          A variant is kept when one of its transcripts has any of the ticked consequences or VEP
          impacts, or reaches the SpliceAI minimum. The transcript filters and the identifiers below
          must hold on that same transcript.
        </p>
        <div className="variant-annotation-layout">
          {CONSEQUENCE_GROUP_ROWS.map((row) => (
            <div key={row[0].id} className="variant-annotation-row">
              {row.map((group) => (
                <AnnotationGroupColumn
                  key={group.id}
                  group={group}
                  selected={selectedEffectValues}
                  onChange={setEffects}
                  className={group.id === 'other' ? 'variant-annotation-group--wide' : undefined}
                />
              ))}
            </div>
          ))}
          <div className="variant-annotation-row">
            <div className="variant-annotation-group">
              <label className="variant-annotation-field">
                <ColumnTitle
                  label="SpliceAI ≥"
                  hint="Keeps a variant whose SpliceAI delta score (0 to 1) reaches this minimum, whatever its consequence. In silico reads it as one of its predictors too."
                />
                <input
                  name="min_spliceai"
                  inputMode="decimal"
                  placeholder="e.g. 0.2"
                  value={draftFilters.min_spliceai}
                  onChange={handleDraftFieldChange}
                />
              </label>
            </div>
            {REGULATORY_CONSEQUENCE_GROUPS.map((group) => (
              <AnnotationGroupColumn
                key={group.id}
                group={group}
                selected={selectedEffectValues}
                onChange={setEffects}
              />
            ))}
            <AnnotationGroupColumn
              group={impactGroup}
              selected={selectedImpactValues}
              onChange={setImpacts}
              wholeGroupBox={false}
            />
            <div className="variant-annotation-group" role="group" aria-label="Transcripts">
              <span className="variant-annotation-group-title">Transcripts</span>
              <div className="variant-annotation-options">
                <AnnotationCheckbox
                  label="Canonical only"
                  hint="Only the transcript VEP flags as canonical."
                  checked={draftFilters.canonical_only === 'true'}
                  onChange={(checked) => setDraftFilterValue('canonical_only', checked ? 'true' : '')}
                />
                <AnnotationCheckbox
                  label="MANE only"
                  hint="Only a MANE Select or MANE Plus Clinical transcript. VEP sets these on GRCh38 only."
                  checked={draftFilters.mane_only === 'true'}
                  onChange={(checked) => setDraftFilterValue('mane_only', checked ? 'true' : '')}
                />
                <AnnotationCheckbox
                  label="LoF only"
                  hint="Only a transcript with a loss-of-function (LoF) call in its annotation."
                  checked={draftFilters.lof_only === 'true'}
                  onChange={(checked) => setDraftFilterValue('lof_only', checked ? 'true' : '')}
                />
              </div>
            </div>
          </div>
          {heldEffectGroup.options.length ? (
            <div className="variant-annotation-row">
              <AnnotationGroupColumn
                group={heldEffectGroup}
                selected={selectedEffectValues}
                onChange={setEffects}
                wholeGroupBox={false}
              />
            </div>
          ) : null}
        </div>

        <div className="analysis-filter-grid analysis-filter-grid--4">
          {offers('transcript') ? (
            <input
              name="transcript"
              placeholder="Transcript"
              value={draftFilters.transcript}
              onChange={handleDraftFieldChange}
            />
          ) : null}
          <input
            name="rsid"
            placeholder="dbSNP / rsID"
            value={draftFilters.rsid}
            onChange={handleDraftFieldChange}
          />
          <input
            name="hgvsc"
            placeholder="HGVS.c"
            value={draftFilters.hgvsc}
            onChange={handleDraftFieldChange}
          />
          <input
            name="hgvsp"
            placeholder="HGVS.p"
            value={draftFilters.hgvsp}
            onChange={handleDraftFieldChange}
          />
        </div>
      </div>
    </FilterSection>
  );
};

export const InSilicoFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    openSections,
    summarizeSection,
    toggleSingleValueCheckbox,
  } = form;
  const inSilicoFilterCount = SECTION_FILTER_COUNTS.inSilico(form);
  return (
    <FilterSection
      title="In silico"
      open={openSections.inSilico}
      onToggle={handleSectionToggle('inSilico')}
      meta={summarizeSection(inSilicoFilterCount, 'No filters')}
    >
      <div className="variant-filter-dropdown-content">
        <div className="analysis-filter-grid analysis-filter-grid--5">
          <input
            name="min_cadd"
            placeholder="CADD ≥"
            value={draftFilters.min_cadd}
            onChange={handleDraftFieldChange}
          />
          <input
            name="min_revel"
            placeholder="REVEL ≥"
            value={draftFilters.min_revel}
            onChange={handleDraftFieldChange}
          />
        </div>
        <div className="variant-inline-controls">
          <div>
            <p className="variant-annotation-impact-title">SIFT</p>
            <div className="variant-checkbox-grid variant-checkbox-grid--small">
              {SIFT_VALUE_OPTIONS.map((option) => (
                <label key={option} className="analysis-checkbox variant-compact-checkbox">
                  <input
                    type="checkbox"
                    checked={draftFilters.sift === option}
                    onChange={() => toggleSingleValueCheckbox('sift', option)}
                  />
                  {option.replace(/_/g, ' ')}
                </label>
              ))}
            </div>
          </div>
          <div>
            <p className="variant-annotation-impact-title">PolyPhen</p>
            <div className="variant-checkbox-grid variant-checkbox-grid--small">
              {POLYPHEN_VALUE_OPTIONS.map((option) => (
                <label key={option} className="analysis-checkbox variant-compact-checkbox">
                  <input
                    type="checkbox"
                    checked={draftFilters.polyphen === option}
                    onChange={() => toggleSingleValueCheckbox('polyphen', option)}
                  />
                  {option.replace(/_/g, ' ')}
                </label>
              ))}
            </div>
          </div>
        </div>
        <p className="table-subtle">
          Variants matching any selected predictor are returned. Numeric thresholds are
          interpreted as greater-than-or-equal filters. The SpliceAI minimum, set under
          Annotations, counts as one of these predictors too.
        </p>
      </div>
    </FilterSection>
  );
};

// The population frequency maximums, as seqr's columns: one slider per population.
const FREQUENCY_POPULATIONS: readonly {
  key: keyof SmallFilterState;
  label: string;
  hint: string;
}[] = [
  {
    key: 'max_gnomad_genomes_af',
    label: 'gnomAD genomes',
    hint: 'Maximum allele frequency in gnomAD genomes. A variant gnomAD genomes lacks passes.',
  },
  {
    key: 'max_gnomad_exomes_af',
    label: 'gnomAD exomes',
    hint: 'Maximum allele frequency in gnomAD exomes. A variant gnomAD exomes lacks passes.',
  },
  {
    key: 'max_gnomad_popmax_af',
    label: 'Popmax',
    hint: "Maximum allele frequency in the population where the variant is most common: gnomAD's popmax (grpmax), else VEP's MAX_AF (every 1000 Genomes and gnomAD population). A variant without either passes.",
  },
  {
    key: 'max_gnomad_af',
    label: 'gnomAD combined',
    hint: "Maximum of gnomAD's combined allele frequency; without one, the higher of the exome and genome frequencies. A variant without either passes.",
  },
  {
    key: 'max_topmed_af',
    label: 'TOPMed',
    hint: 'Maximum allele frequency in TOPMed. A variant TOPMed lacks passes.',
  },
];

/** The value the fields share ('' when none is set), or null when the set ones differ. */
const sharedFilterValue = (values: readonly string[]) => {
  const set = values.map((value) => value.trim()).filter(Boolean);
  if (!set.length) return '';
  return set.every((value) => Number(value) === Number(set[0])) ? set[0] : null;
};

export const FrequencyFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    openSections,
    setDraftFilterValue,
    summarizeSection,
  } = form;
  const frequencyFilterCount = SECTION_FILTER_COUNTS.frequency(form);

  // The header sets every population at once, as seqr's does; it shows the value the set
  // populations share, and "mixed" when they differ.
  const sharedAf = sharedFilterValue(FREQUENCY_POPULATIONS.map(({ key }) => draftFilters[key]));
  const afStepValues = AF_SLIDER_STEPS.filter((step) => step.value !== '').map((step) => step.value);
  const headerAf =
    sharedAf === null
      ? 'mixed'
      : (afStepValues.find((value) => sharedAf !== '' && Number(value) === Number(sharedAf)) ??
        sharedAf);
  const sharedHomHemi = sharedFilterValue([
    draftFilters.max_gnomad_hom_count,
    draftFilters.max_gnomad_hemi_count,
  ]);
  const setAllAf = (value: string) =>
    FREQUENCY_POPULATIONS.forEach(({ key }) => setDraftFilterValue(key, value));
  const setHomHemi = (value: string) => {
    setDraftFilterValue('max_gnomad_hom_count', value);
    setDraftFilterValue('max_gnomad_hemi_count', value);
  };
  return (
    <FilterSection
      title="Frequency"
      open={openSections.frequency}
      onToggle={handleSectionToggle('frequency')}
      meta={summarizeSection(frequencyFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>AF</span>
            <select
              aria-label="Maximum AF, all populations"
              value={headerAf}
              onChange={(event) => setAllAf(event.target.value)}
            >
              <option value="">Any</option>
              {afStepValues.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
              {headerAf !== 'mixed' && headerAf !== '' && !afStepValues.includes(headerAf) ? (
                <option value={headerAf}>{headerAf}</option>
              ) : null}
              {headerAf === 'mixed' ? (
                <option value="mixed" disabled>
                  Mixed
                </option>
              ) : null}
            </select>
          </label>
          <label className="variant-summary-select-field variant-summary-input-field">
            <span>AC</span>
            <input
              aria-label="Maximum gnomAD AC"
              name="max_gnomad_ac"
              inputMode="numeric"
              value={draftFilters.max_gnomad_ac}
              onChange={handleDraftFieldChange}
            />
          </label>
          <label className="variant-summary-select-field variant-summary-input-field">
            <span>H/H</span>
            <input
              aria-label="Maximum gnomAD homozygotes and hemizygotes"
              inputMode="numeric"
              placeholder={sharedHomHemi === null ? 'mixed' : undefined}
              value={sharedHomHemi ?? ''}
              onChange={(event) => setHomHemi(event.target.value)}
            />
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="variant-filter-section-note">
          Filter on the allele frequency in each population (AF), and on gnomAD&apos;s allele count
          (AC) and homozygote and hemizygote counts (H/H). A variant a population lacks passes its
          maximum, as absent from it.
        </p>
        <div className="variant-frequency-columns">
          {FREQUENCY_POPULATIONS.map(({ key, label, hint }) => (
            <div key={key} className="variant-frequency-column">
              <ColumnTitle label={label} hint={hint} />
              <StepSlider
                name={`${label} maximum AF`}
                steps={AF_SLIDER_STEPS}
                value={draftFilters[key]}
                onChange={(value) => setDraftFilterValue(key, value)}
              />
            </div>
          ))}
          <div className="variant-frequency-column">
            <ColumnTitle
              label="gnomAD counts"
              hint="gnomAD's totals: its combined count, else the exome and genome counts added. A variant without a count passes, as absent from gnomAD."
            />
            <div className="variant-frequency-counts">
              <label className="variant-inline-field">
                <span>AC ≤</span>
                <input
                  name="max_gnomad_ac"
                  inputMode="numeric"
                  value={draftFilters.max_gnomad_ac}
                  onChange={handleDraftFieldChange}
                />
              </label>
              <label className="variant-inline-field">
                <span>Hom ≤</span>
                <input
                  name="max_gnomad_hom_count"
                  inputMode="numeric"
                  value={draftFilters.max_gnomad_hom_count}
                  onChange={handleDraftFieldChange}
                />
              </label>
              <label className="variant-inline-field">
                <span>Hemi ≤</span>
                <input
                  name="max_gnomad_hemi_count"
                  inputMode="numeric"
                  value={draftFilters.max_gnomad_hemi_count}
                  onChange={handleDraftFieldChange}
                />
              </label>
            </div>
          </div>
        </div>
        <label className="analysis-checkbox">
          <input
            type="checkbox"
            checked={draftFilters.clinvar_overrides_frequency === 'true'}
            onChange={(event) =>
              setDraftFilterValue(
                'clinvar_overrides_frequency',
                event.target.checked ? 'true' : '',
              )
            }
          />
          <span>ClinVar pathogenic / likely pathogenic overrules the frequency filter</span>
        </label>
      </div>
    </FilterSection>
  );
};

type QualityField = 'qual' | 'dp' | 'af' | 'ad_alt';

// The per-member call-quality minimums, in seqr's order: genotype quality and allele balance
// first. `af` is the allele fraction of the member's call (its allele balance).
const QUALITY_FIELDS: readonly {
  key: QualityField;
  label: string;
  column: string;
  steps: readonly SliderStep[];
  hint: string;
}[] = [
  {
    key: 'qual',
    label: 'Genotype quality',
    column: 'GQ ≥',
    steps: GQ_SLIDER_STEPS,
    hint: "Minimum genotype quality (GQ) of the member's call.",
  },
  {
    key: 'af',
    label: 'Allele balance',
    column: 'AB ≥',
    steps: AB_SLIDER_STEPS,
    hint: "Minimum share of the member's reads that carry the alternate allele (the call's allele fraction).",
  },
  {
    key: 'dp',
    label: 'Depth',
    column: 'DP ≥',
    steps: DP_SLIDER_STEPS,
    hint: "Minimum read depth (DP) of the member's call.",
  },
  {
    key: 'ad_alt',
    label: 'Alt reads',
    column: 'AD alt ≥',
    steps: AD_SLIDER_STEPS,
    hint: "Minimum number of the member's reads that carry the alternate allele (AD).",
  },
];

type QualityThresholds = Record<QualityField, string>;

// The quick choices, as seqr's: High quality and Passing also keep only the variants whose
// calls passed their callers' FILTER (the owner's decision of 2026-11-03).
const CALL_QUALITY_PRESETS: Record<'high_quality' | 'all_passing', QualityThresholds> = {
  high_quality: { qual: '20', dp: '10', af: '0.2', ad_alt: '4' },
  all_passing: { qual: '15', dp: '8', af: '0.18', ad_alt: '3' },
};
const NO_QUALITY_THRESHOLDS: QualityThresholds = { qual: '', dp: '', af: '', ad_alt: '' };

const hasQualityThreshold = (filter: SmallVariantSampleFilter | undefined) =>
  QUALITY_FIELDS.some(({ key }) => (filter?.[key] ?? '').trim() !== '');

export const CallQualityFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleSampleFieldChange,
    handleSectionToggle,
    members,
    openSections,
    sampleDraftFilters,
    setDraftFilterValue,
    summarizeSection,
  } = form;
  const passOnly = draftFilters.pass_only === 'true';
  // seqr's "Affected only": the minimums apply to the affected members alone, so an
  // unaffected parent's reference call is not held to an allele balance it cannot have. It
  // reads off the draft: on while no unaffected member has a minimum and an affected one
  // does; with no minimum set at all, as last chosen (on at first).
  const [preferAffectedOnly, setPreferAffectedOnly] = useState(true);
  const affectedMembers = members.filter((member) => member.affected);
  const unaffectedMembers = members.filter((member) => !member.affected);
  const canLimitToAffected = affectedMembers.length > 0 && unaffectedMembers.length > 0;
  const holdsMinimum = (member: FamilyMember) => hasQualityThreshold(sampleDraftFilters[member.sample_id]);
  const affectedOnly =
    canLimitToAffected &&
    !unaffectedMembers.some(holdsMinimum) &&
    (affectedMembers.some(holdsMinimum) || preferAffectedOnly);
  const targets = affectedOnly ? affectedMembers : members;

  const valueOf = (member: FamilyMember, key: QualityField) =>
    (sampleDraftFilters[member.sample_id]?.[key] ?? '').trim();
  // The value the members a control sets share ('' when none), or null when they differ.
  const sharedValue = (key: QualityField) => {
    const values = targets.map((member) => valueOf(member, key));
    return values.every((value) => value === values[0]) ? (values[0] ?? '') : null;
  };
  const setForMembers = (memberList: readonly FamilyMember[], key: QualityField, value: string) =>
    memberList.forEach((member) => handleSampleFieldChange(member.sample_id, key, value));
  const setThresholds = (thresholds: QualityThresholds) =>
    QUALITY_FIELDS.forEach(({ key }) => setForMembers(targets, key, thresholds[key]));

  const toggleAffectedOnly = (checked: boolean) => {
    setPreferAffectedOnly(checked);
    QUALITY_FIELDS.forEach(({ key }) => {
      if (checked) {
        setForMembers(unaffectedMembers, key, '');
        return;
      }
      // Off: the minimums the affected members share now hold for every member.
      const values = affectedMembers.map((member) => valueOf(member, key));
      if (values.every((value) => value === values[0])) {
        setForMembers(unaffectedMembers, key, values[0] ?? '');
      }
    });
  };

  const matches = (thresholds: QualityThresholds, withPassOnly: boolean) =>
    passOnly === withPassOnly &&
    targets.every((member) =>
      QUALITY_FIELDS.every(({ key }) => {
        const value = valueOf(member, key);
        return thresholds[key] === '' ? value === '' : value !== '' && Number(value) === Number(thresholds[key]);
      }),
    ) && members.filter((member) => !targets.includes(member)).every((member) => !holdsMinimum(member));
  const selectedQuickFilter = matches(NO_QUALITY_THRESHOLDS, false)
    ? 'all_variants'
    : matches(CALL_QUALITY_PRESETS.high_quality, true)
      ? 'high_quality'
      : matches(CALL_QUALITY_PRESETS.all_passing, true)
        ? 'all_passing'
        : 'custom';
  const applyQuickFilter = (value: string) => {
    if (value === 'all_variants') {
      QUALITY_FIELDS.forEach(({ key }) => setForMembers(members, key, ''));
      setDraftFilterValue('pass_only', '');
      return;
    }
    if (value === 'high_quality' || value === 'all_passing') {
      setThresholds(CALL_QUALITY_PRESETS[value]);
      setDraftFilterValue('pass_only', 'true');
    }
  };

  return (
    <FilterSection
      title="Call quality"
      open={openSections.callQuality}
      onToggle={handleSectionToggle('callQuality')}
      meta={summarizeSection(SECTION_FILTER_COUNTS.callQuality(form), 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Quality</span>
            <select
              aria-label="Quick call quality"
              value={selectedQuickFilter}
              onChange={(event) => applyQuickFilter(event.target.value)}
            >
              <option value="all_variants">All variants</option>
              <option value="high_quality">High quality</option>
              <option value="all_passing">Passing</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="variant-filter-section-note">
          Minimum quality of each member&apos;s call: genotype quality (GQ), allele balance (AB, the
          share of reads with the alternate allele), depth (DP) and alternate-allele reads (AD).
          Set them here for the members at once, or per member below. Pass variants only keeps a
          variant when every call that carries it passed its caller&apos;s FILTER.
        </p>
        <div className="variant-quality-columns">
          <div className="variant-quality-column">
            <ColumnTitle
              label="Affected only"
              hint="Apply the minimums to the affected members only; the unaffected members' calls are not held to them. Off, they hold for every member."
            />
            <label className="variant-switch">
              <input
                type="checkbox"
                role="switch"
                aria-label="Affected only"
                checked={affectedOnly}
                disabled={!canLimitToAffected}
                onChange={(event) => toggleAffectedOnly(event.target.checked)}
              />
              <span className="variant-switch-track" aria-hidden="true" />
            </label>
          </div>
          <div className="variant-quality-column" role="radiogroup" aria-label="Filter value">
            <ColumnTitle
              label="Filter value"
              hint="Pass variants only keeps a variant when every member's call that carries it passed its caller's FILTER (PASS, or no filter set), whatever Affected only says. A callset of one file per sample reads each call's own FILTER, so a parent's reference call (RefCall) holds back nothing; a multi-sample VCF reads the FILTER of the site."
            />
            <div className="variant-radio-options">
              <label className="analysis-checkbox">
                <input
                  type="radio"
                  name="pass_only"
                  checked={!passOnly}
                  onChange={() => setDraftFilterValue('pass_only', '')}
                />
                Show all variants
              </label>
              <label className="analysis-checkbox">
                <input
                  type="radio"
                  name="pass_only"
                  checked={passOnly}
                  onChange={() => setDraftFilterValue('pass_only', 'true')}
                />
                Pass variants only
              </label>
            </div>
          </div>
          {QUALITY_FIELDS.map(({ key, label, steps, hint }) => (
            <div key={key} className="variant-quality-column">
              <ColumnTitle label={label} hint={hint} />
              <StepSlider
                name={`${label} minimum`}
                steps={steps}
                value={sharedValue(key)}
                onChange={(value) => setForMembers(targets, key, value)}
              />
            </div>
          ))}
        </div>
        <div className="variant-quality-table-shell">
          <table className="variant-quality-table">
            <thead>
              <tr>
                <th scope="col">Member</th>
                {QUALITY_FIELDS.map(({ key, column }) => (
                  <th key={key} scope="col">
                    {column}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {members.map((member) => {
                const sample = member.sample_id;
                const sexSymbol = member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚧';
                return (
                  <tr key={sample}>
                    <th scope="row">
                      <span className="variant-sample-title">
                        {sexSymbol} {sample}
                      </span>{' '}
                      <span className="table-chip">{member.role}</span>{' '}
                      <span className={`table-chip ${member.affected ? 'badge-chip--signature' : ''}`}>
                        {member.affected ? 'affected' : 'unaffected'}
                      </span>
                    </th>
                    {QUALITY_FIELDS.map(({ key, column }) => (
                      <td key={key}>
                        <input
                          aria-label={`${sample} ${column}`}
                          inputMode="decimal"
                          value={sampleDraftFilters[sample]?.[key] ?? ''}
                          onChange={(event) => handleSampleFieldChange(sample, key, event.target.value)}
                        />
                      </td>
                    ))}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </FilterSection>
  );
};

export const LocationsFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    draftLocationProblems,
    handleDraftFieldChange,
    handleSectionToggle,
    offers,
    openSections,
    panels,
    summarizeSection,
  } = form;
  const locationFilterCount = SECTION_FILTER_COUNTS.locations(form);
  return (
    <FilterSection
      title="Locations"
      open={openSections.locations}
      onToggle={handleSectionToggle('locations')}
      meta={summarizeSection(locationFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Panel</span>
            <select
              aria-label="Quick gene panel"
              name="panel_id"
              value={draftFilters.panel_id}
              onChange={handleDraftFieldChange}
            >
              <option value="">Any gene panel</option>
              {panels.map((panel) => (
                <option key={panel._id} value={panel._id}>
                  {panel.name}
                </option>
              ))}
              {/* An applied panel missing from the list (it failed to load) still shows as
                  applied, not as "Any gene panel" (#606). */}
              {draftFilters.panel_id && !panels.some((panel) => panel._id === draftFilters.panel_id) ? (
                <option value={draftFilters.panel_id}>{`Panel ${draftFilters.panel_id} (not in the panel list)`}</option>
              ) : null}
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <textarea
          name="gene"
          rows={3}
          placeholder="Gene list: BRCA1&#10;BRCA2&#10;TP53"
          value={draftFilters.gene}
          onChange={handleDraftFieldChange}
        />
        {offers('intervals') ? (
          <textarea
            name="intervals"
            rows={3}
            placeholder="Intervals: chr13:32315086-32400266&#10;chr17:43044295-43125482"
            value={draftFilters.intervals}
            onChange={handleDraftFieldChange}
          />
        ) : null}
        <LocationProblemList problems={draftLocationProblems?.include ?? []} />
      </div>
    </FilterSection>
  );
};

export const ExcludeFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    clinvarOptions,
    customTagOptions,
    draftFilters,
    draftLocationProblems,
    handleDraftFieldChange,
    handleSectionToggle,
    offers,
    openSections,
    selectedHeldTagOptions,
    setDraftFilterValue,
    standardTagOptions,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  const excludeFilterCount = SECTION_FILTER_COUNTS.exclude(form);
  const selectedExcludeClinvarValues = parseCommaSeparatedValues(draftFilters.exclude_clinvar);
  const selectedExcludeReviewTagValues = parseCommaSeparatedValues(draftFilters.exclude_review_tags);
  const excludeCustomTagOptions = [
    ...customTagOptions,
    ...selectedHeldTagOptions(selectedExcludeReviewTagValues),
  ];
  const applyExcludeQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('exclude_clinvar', '');
      setDraftFilterValue('exclude_review_tags', '');
      return;
    }
    if (value === 'benign_likely_benign') {
      setDraftFilterValue('exclude_clinvar', EXCLUDE_QUICK_CLINVAR_FILTER);
      setDraftFilterValue('exclude_review_tags', '');
      return;
    }
    if (value === 'excluded_tag') {
      setDraftFilterValue('exclude_clinvar', '');
      setDraftFilterValue('exclude_review_tags', COLLABORATION_QUICK_TAGS.excluded);
      return;
    }
    if (value === 'excluded_and_benign') {
      setDraftFilterValue('exclude_clinvar', EXCLUDE_QUICK_CLINVAR_FILTER);
      setDraftFilterValue('exclude_review_tags', COLLABORATION_QUICK_TAGS.excluded);
    }
  };

  const selectedExcludeQuickFilter = (() => {
    const selectedClinvar = new Set(parseCommaSeparatedValues(draftFilters.exclude_clinvar));
    const selectedTags = new Set(parseCommaSeparatedValues(draftFilters.exclude_review_tags));
    const hasOnlyBenignClinvar =
      selectedClinvar.size === EXCLUDE_QUICK_CLINVAR_VALUES.length &&
      EXCLUDE_QUICK_CLINVAR_VALUES.every((value) => selectedClinvar.has(value));
    const hasOnlyExcludedTag =
      selectedTags.size === 1 && selectedTags.has(COLLABORATION_QUICK_TAGS.excluded);

    if (selectedClinvar.size === 0 && selectedTags.size === 0) return 'all';
    if (hasOnlyBenignClinvar && selectedTags.size === 0) return 'benign_likely_benign';
    if (selectedClinvar.size === 0 && hasOnlyExcludedTag) return 'excluded_tag';
    if (hasOnlyBenignClinvar && hasOnlyExcludedTag) return 'excluded_and_benign';
    return 'custom';
  })();
  return (
    <FilterSection
      title="Exclude"
      open={openSections.exclude}
      onToggle={handleSectionToggle('exclude')}
      meta={summarizeSection(excludeFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Quick</span>
            <select
              aria-label="Quick exclude"
              value={selectedExcludeQuickFilter}
              onChange={(event) => applyExcludeQuickFilter(event.target.value)}
            >
              <option value="all">None</option>
              <option value="benign_likely_benign">Benign/Likely benign</option>
              {offers('exclude_review_tags') ? (
                <>
                  <option value="excluded_tag">Excluded tag</option>
                  <option value="excluded_and_benign">Excluded + benign</option>
                </>
              ) : null}
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <p className="variant-annotation-impact-title">Excluded ClinVar status</p>
        <div className="variant-checkbox-grid variant-checkbox-grid--small">
          {clinvarOptions.map((option) => (
            <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
              <input
                type="checkbox"
                checked={selectedExcludeClinvarValues.includes(option.value)}
                onChange={() => toggleDraftFilterListValue('exclude_clinvar', option.value)}
              />
              {option.label}
            </label>
          ))}
        </div>
        {offers('exclude_review_tags') ? (
          <div className="variant-review-curation-columns">
            <div>
              <p className="variant-annotation-impact-title">Excluded standard tags</p>
              {standardTagOptions.length ? (
                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                  {standardTagOptions.map((option) => (
                    <label
                      key={option.value}
                      className="analysis-checkbox variant-compact-checkbox"
                    >
                      <input
                        type="checkbox"
                        checked={selectedExcludeReviewTagValues.includes(option.value)}
                        onChange={() =>
                          toggleDraftFilterListValue('exclude_review_tags', option.value)
                        }
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              ) : (
                <p className="table-subtle">No standard tags available.</p>
              )}
            </div>
            <div>
              <p className="variant-annotation-impact-title">Excluded custom tags</p>
              {excludeCustomTagOptions.length ? (
                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                  {excludeCustomTagOptions.map((option) => (
                    <label
                      key={option.value}
                      className="analysis-checkbox variant-compact-checkbox"
                    >
                      <input
                        type="checkbox"
                        checked={selectedExcludeReviewTagValues.includes(option.value)}
                        onChange={() =>
                          toggleDraftFilterListValue('exclude_review_tags', option.value)
                        }
                      />
                      {option.label}
                    </label>
                  ))}
                </div>
              ) : (
                <p className="table-subtle">No custom tags available.</p>
              )}
            </div>
          </div>
        ) : null}
        {offers('exclude_gene') ? (
          <textarea
            name="exclude_gene"
            rows={3}
            placeholder="Excluded genes: TTN&#10;MUC4"
            value={draftFilters.exclude_gene}
            onChange={handleDraftFieldChange}
          />
        ) : null}
        {offers('exclude_intervals') ? (
          <textarea
            name="exclude_intervals"
            rows={3}
            placeholder="Excluded intervals: chr1:1000-5000"
            value={draftFilters.exclude_intervals}
            onChange={handleDraftFieldChange}
          />
        ) : null}
        <LocationProblemList problems={draftLocationProblems?.exclude ?? []} />
      </div>
    </FilterSection>
  );
};

export const ReviewFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    customTagOptions,
    draftFilters,
    handleSectionToggle,
    offers,
    openSections,
    selectedHeldTagOptions,
    setDraftFilterValue,
    standardTagOptions,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  // A search without the classification filter (the NIPT list) offers the review tags only.
  const offersClassification = offers('classification');
  const reviewFilterCount = SECTION_FILTER_COUNTS.review(form);

  const selectedClassificationValues = parseCommaSeparatedValues(draftFilters.classification);
  const selectedReviewTagValues = parseCommaSeparatedValues(draftFilters.review_tags);
  const reviewCustomTagOptions = [
    ...customTagOptions,
    ...selectedHeldTagOptions(selectedReviewTagValues),
  ];
  const classificationOptions = REVIEW_CLASSIFICATION_OPTIONS.map((option) => ({
    value: option,
    label: option,
  }));
  const applyReviewQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('classification', '');
      setDraftFilterValue('review_tags', '');
      setDraftFilterValue('has_notes', '');
      return;
    }
    if (value === 'pathogenic_vus') {
      setDraftFilterValue('classification', REVIEW_QUICK_CLASSIFICATION_FILTER);
      setDraftFilterValue('review_tags', '');
      setDraftFilterValue('has_notes', '');
      return;
    }
    if (value === 'review_tag') {
      setDraftFilterValue('classification', '');
      setDraftFilterValue('review_tags', COLLABORATION_QUICK_TAGS.review);
      setDraftFilterValue('has_notes', '');
    }
  };

  const selectedReviewQuickFilter = (() => {
    const selectedClassifications = new Set(parseCommaSeparatedValues(draftFilters.classification));
    const selectedTags = new Set(parseCommaSeparatedValues(draftFilters.review_tags));
    const hasNoNotesFilter = draftFilters.has_notes !== 'true';
    const hasOnlyPathogenicVus =
      selectedClassifications.size === REVIEW_QUICK_CLASSIFICATION_VALUES.length &&
      REVIEW_QUICK_CLASSIFICATION_VALUES.every((value) => selectedClassifications.has(value));
    const hasOnlyReviewTag =
      selectedTags.size === 1 && selectedTags.has(COLLABORATION_QUICK_TAGS.review);

    if (selectedClassifications.size === 0 && selectedTags.size === 0 && hasNoNotesFilter) {
      return 'all';
    }
    if (hasOnlyPathogenicVus && selectedTags.size === 0 && hasNoNotesFilter) {
      return 'pathogenic_vus';
    }
    if (selectedClassifications.size === 0 && hasOnlyReviewTag && hasNoNotesFilter) {
      return 'review_tag';
    }
    return 'custom';
  })();
  return (
    <FilterSection
      title="Review and curation"
      open={openSections.review}
      onToggle={handleSectionToggle('review')}
      meta={summarizeSection(reviewFilterCount, 'No filters')}
      controls={
        <>
          <label className="variant-summary-select-field">
            <span>Quick</span>
            <select
              aria-label="Quick review"
              value={selectedReviewQuickFilter}
              onChange={(event) => applyReviewQuickFilter(event.target.value)}
            >
              <option value="all">All</option>
              {offersClassification ? <option value="pathogenic_vus">P/LP/VUS</option> : null}
              <option value="review_tag">Review tag</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
      <div className="variant-filter-dropdown-content">
        <div className="variant-review-curation-columns">
          {offersClassification ? (
            <div>
              <p className="variant-annotation-impact-title">Classification</p>
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {classificationOptions.map((option) => (
                  <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={selectedClassificationValues.includes(option.value)}
                      onChange={() =>
                        toggleDraftFilterListValue('classification', option.value)
                      }
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
          ) : null}
          <div>
            <p className="variant-annotation-impact-title">Standard tags</p>
            <div className="variant-checkbox-grid variant-checkbox-grid--small">
              {standardTagOptions.map((option) => (
                <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                  <input
                    type="checkbox"
                    checked={selectedReviewTagValues.includes(option.value)}
                    onChange={() => toggleDraftFilterListValue('review_tags', option.value)}
                  />
                  {option.label}
                </label>
              ))}
            </div>
          </div>
          <div>
            <p className="variant-annotation-impact-title">Custom tags</p>
            {reviewCustomTagOptions.length ? (
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {reviewCustomTagOptions.map((option) => (
                  <label
                    key={option.value}
                    className="analysis-checkbox variant-compact-checkbox"
                  >
                    <input
                      type="checkbox"
                      checked={selectedReviewTagValues.includes(option.value)}
                      onChange={() => toggleDraftFilterListValue('review_tags', option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            ) : (
              <p className="table-subtle">No custom tags available.</p>
            )}
          </div>
        </div>

        {offers('has_notes') ? (
          <label className="analysis-checkbox">
            <input
              type="checkbox"
              checked={draftFilters.has_notes === 'true'}
              onChange={(event) =>
                setDraftFilterValue('has_notes', event.target.checked ? 'true' : '')
              }
            />
            Only show variants with saved notes
          </label>
        ) : null}

      </div>
    </FilterSection>
  );
};
