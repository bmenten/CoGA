import { useState } from 'react';
import type { ChangeEvent, SyntheticEvent } from 'react';
import {
  ALL_GT_GROUPS,
  BUILT_IN_SMALL_PRESETS,
  COLLABORATION_QUICK_TAGS,
  REVIEW_CLASSIFICATION_OPTIONS,
  sortTagDefinitions,
  resolveCarrierScreeningCoupleMembers,
  type ActiveSmallFilterChip,
  type FamilyMember,
  type GenePanel,
  type SmallFilterState,
  type SmallVariantFilterPreset,
  type SmallPreset,
  type SmallVariantSearchState,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';

type SmallVariantFilterFormProps = Pick<
  SmallVariantSearchState,
  | 'activeFilterChips'
  | 'applyPreset'
  | 'applySavedPreset'
  | 'draftFilters'
  | 'handleApply'
  | 'handleGtToggle'
  | 'handleReset'
  | 'handleSampleFieldChange'
  | 'members'
  | 'removeActiveFilterChip'
  | 'sampleDraftFilters'
  | 'setDraftFilterValue'
  | 'toggleDraftFilterListValue'
> & {
  panels: GenePanel[];
  presets: SmallVariantFilterPreset[];
  tags: SmallVariantTagDefinition[];
  onSaveCurrentPreset: (payload: {
    name: string;
    description?: string;
  }) => Promise<void>;
  savingPreset?: boolean;
  feedback?: {
    tone: 'error' | 'success';
    message: string;
  } | null;
};

const TYPE_OPTIONS = ['', 'SNV', 'INDEL', 'MNV'];
const CLINVAR_OPTIONS = [
  'Pathogenic',
  'Likely pathogenic',
  'Uncertain significance',
  'Likely benign',
  'Benign',
  'Conflicting classifications',
] as const;
const REVIEW_QUICK_CLASSIFICATION_VALUES = [
  'Pathogenic - class 5',
  'Likely Pathogenic - class 4',
  'VUS - class 3',
] as const;
const REVIEW_QUICK_CLASSIFICATION_FILTER = REVIEW_QUICK_CLASSIFICATION_VALUES.join(', ');
const EXCLUDE_QUICK_CLINVAR_VALUES = ['Benign', 'Likely benign'] as const;
const EXCLUDE_QUICK_CLINVAR_FILTER = EXCLUDE_QUICK_CLINVAR_VALUES.join(', ');
const FREQUENCY_QUICK_GNOMAD_AF = '0.01';
const FREQUENCY_QUICK_GNOMAD_COUNT = '10';
const SIFT_OPTIONS = ['', 'deleterious', 'deleterious_low_confidence', 'tolerated'];
const POLYPHEN_OPTIONS = ['', 'probably_damaging', 'possibly_damaging', 'benign'];
const SIFT_VALUE_OPTIONS = SIFT_OPTIONS.filter((value) => value);
const POLYPHEN_VALUE_OPTIONS = POLYPHEN_OPTIONS.filter((value) => value);

const CONSEQUENCE_BY_IMPACT: Record<string, string[]> = {
  HIGH: [
    'frameshift_variant',
    'stop_gained',
    'stop_lost',
    'start_lost',
    'splice_acceptor_variant',
    'splice_donor_variant',
  ],
  MODERATE: [
    'missense_variant',
    'inframe_insertion',
    'inframe_deletion',
    'protein_altering_variant',
  ],
  LOW: ['synonymous_variant', 'splice_region_variant'],
  MODIFIER: [
    'coding_sequence_variant',
    'splice_donor_5th_base_variant',
    'splice_donor_region_variant',
    'splice_polypyrimidine_tract_variant',
    'intron_variant',
    'motif_feature_variant',
    'TF_binding_site_variant',
    'regulatory_region_variant',
    'upstream_gene_variant',
    'downstream_gene_variant',
    'non_coding_transcript_exon_variant',
  ],
};

const splitSelectedValues = (value: string) =>
  value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

const countNonEmpty = (...values: string[]) => values.filter((value) => value.trim()).length;

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

const GT_GROUP_OPTIONS = [
  { id: 'hom-group', values: ['1/1', '1|1'] },
  { id: 'het-group', values: ['0/1', '1/0', '0|1', '1|0'] },
  { id: 'ref-group', values: ['0/0', '0|0', './.', 'absent'] },
] as const;

type GtGroupId = (typeof GT_GROUP_OPTIONS)[number]['id'];

const SmallVariantFilterForm = ({
  activeFilterChips,
  applyPreset,
  applySavedPreset,
  draftFilters,
  handleApply,
  handleGtToggle,
  handleReset,
  handleSampleFieldChange,
  members,
  panels,
  presets,
  removeActiveFilterChip,
  sampleDraftFilters,
  setDraftFilterValue,
  tags,
  toggleDraftFilterListValue,
  onSaveCurrentPreset,
  savingPreset = false,
  feedback = null,
}: SmallVariantFilterFormProps) => {
  const [selectedQuickPreset, setSelectedQuickPreset] = useState('');
  const [saveOpen, setSaveOpen] = useState(false);
  const [openSections, setOpenSections] = useState({
    inheritance: false,
    pathogenicity: false,
    annotations: false,
    inSilico: false,
    frequency: false,
    locations: false,
    exclude: false,
    review: false,
  });
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  const carrierScreeningCouple = resolveCarrierScreeningCoupleMembers(members);
  const availableBuiltInPresets = BUILT_IN_SMALL_PRESETS.filter(
    (preset) => preset.value !== 'expanded_carrier_screening' || carrierScreeningCouple,
  );

  const handleDraftFieldChange = (
    event: ChangeEvent<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>,
  ) => {
    setDraftFilterValue(event.target.name as keyof SmallFilterState, event.target.value);
  };

  const handleSectionToggle =
    (section: keyof typeof openSections) => (event: SyntheticEvent<HTMLDetailsElement>) => {
      const nextOpen = event.currentTarget.open;
      setOpenSections((prev) => ({
        ...prev,
        [section]: nextOpen,
      }));
    };

  const getActiveChipLabel = (chip: ActiveSmallFilterChip) => {
    if (chip.kind === 'top' && chip.key === 'panel_id' && chip.value) {
      const panel = panels.find((entry) => entry._id === chip.value);
      return `Gene panel: ${panel?.name || chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'review_tags' && chip.value) {
      const tag = tags.find((entry) => entry.key === chip.value);
      return `Review tags: ${tag?.label || chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'clinvar' && chip.value) {
      return `Pathogenicity: ${chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'exclude_clinvar' && chip.value) {
      return `Exclude pathogenicity: ${chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'exclude_review_tags' && chip.value) {
      const tag = tags.find((entry) => entry.key === chip.value);
      return `Exclude review tags: ${tag?.label || chip.value}`;
    }
    return chip.label;
  };

  const activeSampleMemberCount = members.reduce((count, member) => {
    const sampleFilter = sampleDraftFilters[member.sample_id];
    if (!sampleFilter) return count;
    const genotypeActive =
      sampleFilter.gt.length > 0 && sampleFilter.gt.length < ALL_GT_GROUPS.length;
    const thresholdActive = Boolean(
      sampleFilter.qual || sampleFilter.dp || sampleFilter.af || sampleFilter.ad_alt,
    );
    return count + (genotypeActive || thresholdActive ? 1 : 0);
  }, 0);

  const inheritanceFilterCount =
    activeSampleMemberCount +
    (draftFilters.expanded_carrier_screening === 'true' ? 1 : 0) +
    countNonEmpty(draftFilters.inheritance, draftFilters.type, draftFilters.source, draftFilters.ps);

  const pathogenicityFilterCount = splitSelectedValues(draftFilters.clinvar).length;
  const annotationFilterCount =
    splitSelectedValues(draftFilters.impact).length +
    splitSelectedValues(draftFilters.effect).length +
    countNonEmpty(
      draftFilters.transcript,
      draftFilters.rsid,
      draftFilters.hgvsc,
      draftFilters.hgvsp,
      draftFilters.canonical_only,
      draftFilters.mane_only,
      draftFilters.lof_only,
    );
  const inSilicoFilterCount = countNonEmpty(
    draftFilters.min_cadd,
    draftFilters.min_revel,
    draftFilters.min_spliceai,
    draftFilters.sift,
    draftFilters.polyphen,
  );
  const frequencyFilterCount = countNonEmpty(
    draftFilters.max_gnomad_af,
    draftFilters.max_gnomad_exomes_af,
    draftFilters.max_gnomad_genomes_af,
    draftFilters.max_gnomad_popmax_af,
    draftFilters.max_topmed_af,
    draftFilters.max_gnomad_ac,
    draftFilters.max_gnomad_hom_count,
    draftFilters.max_gnomad_hemi_count,
  );
  const locationFilterCount =
    countNonEmpty(draftFilters.panel_id) +
    (draftFilters.gene.trim() ? countTextAreaEntries(draftFilters.gene) : 0) +
    (draftFilters.intervals.trim() ? countTextAreaEntries(draftFilters.intervals) : 0);
  const excludeFilterCount =
    splitSelectedValues(draftFilters.exclude_clinvar).length +
    splitSelectedValues(draftFilters.exclude_review_tags).length +
    (draftFilters.exclude_gene.trim() ? countTextAreaEntries(draftFilters.exclude_gene) : 0) +
    (draftFilters.exclude_intervals.trim()
      ? countTextAreaEntries(draftFilters.exclude_intervals)
      : 0);
  const reviewFilterCount =
    splitSelectedValues(draftFilters.classification).length +
    splitSelectedValues(draftFilters.review_tags).length +
    (draftFilters.has_notes === 'true' ? 1 : 0);

  const selectedImpactValues = splitSelectedValues(draftFilters.impact);
  const selectedEffectValues = splitSelectedValues(draftFilters.effect);
  const selectedClinvarValues = splitSelectedValues(draftFilters.clinvar);
  const selectedExcludeClinvarValues = splitSelectedValues(draftFilters.exclude_clinvar);
  const selectedExcludeReviewTagValues = splitSelectedValues(draftFilters.exclude_review_tags);
  const selectedClassificationValues = splitSelectedValues(draftFilters.classification);
  const selectedReviewTagValues = splitSelectedValues(draftFilters.review_tags);
  const clinvarOptions = CLINVAR_OPTIONS.map((option) => ({
    value: option,
    label: option,
  }));
  const classificationOptions = REVIEW_CLASSIFICATION_OPTIONS.map((option) => ({
    value: option,
    label: option,
  }));
  const sortedTagDefinitions = sortTagDefinitions(tags);
  const standardTagOptions = sortedTagDefinitions
    .filter((tag) => !tag.is_custom)
    .map((tag) => ({
      value: tag.key,
      label: tag.label,
    }));
  const customTagOptions = sortedTagDefinitions
    .filter((tag) => tag.is_custom)
    .map((tag) => ({
      value: tag.key,
      label: tag.label,
    }));

  const summarizeSection = (count: number, emptyLabel: string) =>
    count > 0 ? `${count} active` : emptyLabel;

  const stopSummaryInteraction = (event: SyntheticEvent<HTMLElement>) => {
    event.stopPropagation();
  };

  const toggleSingleValueCheckbox = (key: keyof SmallFilterState, optionValue: string) => {
    setDraftFilterValue(key, draftFilters[key] === optionValue ? '' : optionValue);
  };

  const joinSelectedValues = (values: Iterable<string>) =>
    Array.from(new Set(Array.from(values).map((value) => value.trim()).filter(Boolean))).join(', ');

  const normalizePercentValue = (value: string) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 0) return 0;
    return Math.min(10, parsed);
  };

  const formatPercentFilterValue = (value: string) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed <= 0) return 'Any';
    return `${(parsed * 100).toFixed(2).replace(/\.?0+$/, '')}%`;
  };

  const setFrequencyFromPercent = (key: keyof SmallFilterState, percentValue: number) => {
    if (!Number.isFinite(percentValue) || percentValue <= 0) {
      setDraftFilterValue(key, '');
      return;
    }
    const normalized = Math.min(10, Math.max(0, percentValue)) / 100;
    setDraftFilterValue(key, normalized.toFixed(4).replace(/\.?0+$/, ''));
  };

  const numericFilterEquals = (value: string, target: string) => {
    if (!value.trim()) return false;
    return Number(value) === Number(target);
  };

  const applySelectedQuickPreset = () => {
    if (!selectedQuickPreset) return;
    if (selectedQuickPreset.startsWith('built-in:')) {
      applyPreset(selectedQuickPreset.replace('built-in:', '') as SmallPreset);
      setSelectedQuickPreset('');
      return;
    }

    const preset = presets.find((entry) => `saved:${entry._id}` === selectedQuickPreset);
    if (preset) {
      applySavedPreset(preset);
    }
    setSelectedQuickPreset('');
  };

  const setSampleQualityThresholds = (thresholds: {
    qual: string;
    dp: string;
    af: string;
    ad_alt: string;
  }) => {
    members.forEach((member) => {
      handleSampleFieldChange(member.sample_id, 'qual', thresholds.qual);
      handleSampleFieldChange(member.sample_id, 'dp', thresholds.dp);
      handleSampleFieldChange(member.sample_id, 'af', thresholds.af);
      handleSampleFieldChange(member.sample_id, 'ad_alt', thresholds.ad_alt);
    });
  };

  const setSampleGtGroups = (sampleId: string, targetGroups: ReadonlySet<GtGroupId>) => {
    const current = sampleDraftFilters[sampleId];
    if (!current) return;
    GT_GROUP_OPTIONS.forEach((group) => {
      const currentlySelected = group.values.every((value) => current.gt.includes(value));
      const shouldBeSelected = targetGroups.has(group.id);
      if (currentlySelected !== shouldBeSelected) {
        handleGtToggle(sampleId, group.id, shouldBeSelected);
      }
    });
  };

  const updateInheritanceGenotypes = (
    strategy: (member: FamilyMember) => ReadonlySet<GtGroupId>,
  ) => {
    members.forEach((member) => {
      setSampleGtGroups(member.sample_id, strategy(member));
    });
  };

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

  const handleImpactCategoryToggle = (impact: string, checked: boolean) => {
    const consequences = CONSEQUENCE_BY_IMPACT[impact] ?? [];
    const impactSet = new Set(splitSelectedValues(draftFilters.impact));
    const effectSet = new Set(splitSelectedValues(draftFilters.effect));

    if (checked) {
      impactSet.add(impact);
      consequences.forEach((value) => effectSet.add(value));
    } else {
      impactSet.delete(impact);
      consequences.forEach((value) => effectSet.delete(value));
    }

    setDraftFilterValue('impact', joinSelectedValues(impactSet));
    setDraftFilterValue('effect', joinSelectedValues(effectSet));
  };

  const applyCallQualityQuickFilter = (value: string) => {
    if (value === 'all_variants') {
      setSampleQualityThresholds({ qual: '', dp: '', af: '', ad_alt: '' });
      return;
    }
    if (value === 'all_passing') {
      setSampleQualityThresholds({ qual: '15', dp: '8', af: '0.18', ad_alt: '3' });
      return;
    }
    if (value === 'high_quality') {
      setSampleQualityThresholds({ qual: '20', dp: '10', af: '0.2', ad_alt: '4' });
    }
  };

  const applyFrequencyQuickFilter = (value: string) => {
    if (value === 'all') {
      setDraftFilterValue('max_gnomad_af', '');
      setDraftFilterValue('max_gnomad_exomes_af', '');
      setDraftFilterValue('max_gnomad_genomes_af', '');
      setDraftFilterValue('max_gnomad_popmax_af', '');
      setDraftFilterValue('max_topmed_af', '');
      setDraftFilterValue('max_gnomad_ac', '');
      setDraftFilterValue('max_gnomad_hom_count', '');
      setDraftFilterValue('max_gnomad_hemi_count', '');
      return;
    }
    if (value === 'gnomad_rare') {
      setDraftFilterValue('max_gnomad_af', '');
      setDraftFilterValue('max_gnomad_exomes_af', FREQUENCY_QUICK_GNOMAD_AF);
      setDraftFilterValue('max_gnomad_genomes_af', FREQUENCY_QUICK_GNOMAD_AF);
      setDraftFilterValue('max_gnomad_popmax_af', '');
      setDraftFilterValue('max_topmed_af', '');
      setDraftFilterValue('max_gnomad_ac', '');
      setDraftFilterValue('max_gnomad_hom_count', FREQUENCY_QUICK_GNOMAD_COUNT);
      setDraftFilterValue('max_gnomad_hemi_count', FREQUENCY_QUICK_GNOMAD_COUNT);
    }
  };

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

  const selectedPathogenicityQuickFilter = (() => {
    const selected = new Set(splitSelectedValues(draftFilters.clinvar));
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

  const selectedExcludeQuickFilter = (() => {
    const selectedClinvar = new Set(splitSelectedValues(draftFilters.exclude_clinvar));
    const selectedTags = new Set(splitSelectedValues(draftFilters.exclude_review_tags));
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

  const selectedAnnotationQuickFilter = (() => {
    const impactValues = splitSelectedValues(draftFilters.impact);
    const effectValues = splitSelectedValues(draftFilters.effect);
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

  const selectedCallQualityQuickFilter = (() => {
    const allThresholds = members.map((member) => sampleDraftFilters[member.sample_id]);
    if (
      allThresholds.every(
        (filter) => (filter?.qual ?? '') === '' && (filter?.dp ?? '') === '' && (filter?.af ?? '') === '' && (filter?.ad_alt ?? '') === '',
      )
    ) {
      return 'all_variants';
    }
    if (
      allThresholds.every(
        (filter) =>
          (filter?.qual ?? '') === '20' &&
          (filter?.dp ?? '') === '10' &&
          (filter?.af ?? '') === '0.2' &&
          (filter?.ad_alt ?? '') === '4',
      )
    ) {
      return 'high_quality';
    }
    if (
      allThresholds.every(
        (filter) =>
          (filter?.qual ?? '') === '15' &&
          (filter?.dp ?? '') === '8' &&
          (filter?.af ?? '') === '0.18' &&
          (filter?.ad_alt ?? '') === '3',
      )
    ) {
      return 'all_passing';
    }
    return 'custom';
  })();

  const selectedFrequencyQuickFilter = (() => {
    const allFrequencyValues = [
      draftFilters.max_gnomad_af,
      draftFilters.max_gnomad_exomes_af,
      draftFilters.max_gnomad_genomes_af,
      draftFilters.max_gnomad_popmax_af,
      draftFilters.max_topmed_af,
      draftFilters.max_gnomad_ac,
      draftFilters.max_gnomad_hom_count,
      draftFilters.max_gnomad_hemi_count,
    ];
    if (allFrequencyValues.every((value) => value.trim() === '')) return 'all';
    if (
      !draftFilters.max_gnomad_af.trim() &&
      !draftFilters.max_gnomad_popmax_af.trim() &&
      !draftFilters.max_topmed_af.trim() &&
      !draftFilters.max_gnomad_ac.trim() &&
      numericFilterEquals(draftFilters.max_gnomad_exomes_af, FREQUENCY_QUICK_GNOMAD_AF) &&
      numericFilterEquals(draftFilters.max_gnomad_genomes_af, FREQUENCY_QUICK_GNOMAD_AF) &&
      numericFilterEquals(draftFilters.max_gnomad_hom_count, FREQUENCY_QUICK_GNOMAD_COUNT) &&
      numericFilterEquals(draftFilters.max_gnomad_hemi_count, FREQUENCY_QUICK_GNOMAD_COUNT)
    ) {
      return 'gnomad_rare';
    }
    return 'custom';
  })();

  const selectedReviewQuickFilter = (() => {
    const selectedClassifications = new Set(splitSelectedValues(draftFilters.classification));
    const selectedTags = new Set(splitSelectedValues(draftFilters.review_tags));
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
    <form onSubmit={handleApply} className="space-y-4 variant-search-workspace">
      <div className="variant-search-header">
        <div className="variant-search-meta">
          <div className="variant-search-toolbar">
            <select
              aria-label="Preset or saved search"
              value={selectedQuickPreset}
              onChange={(event) => setSelectedQuickPreset(event.target.value)}
            >
              <option value="">Preset or saved search</option>
              <optgroup label="Built-in presets">
                {availableBuiltInPresets.map((preset) => (
                  <option key={preset.value} value={`built-in:${preset.value}`}>
                    {preset.label}
                  </option>
                ))}
              </optgroup>
              {presets.length ? (
                <optgroup label="Saved searches">
                  {presets.map((preset) => (
                    <option key={preset._id} value={`saved:${preset._id}`}>
                      {preset.name}
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
            <button
              type="button"
              className="button-secondary"
              disabled={!selectedQuickPreset}
              onClick={applySelectedQuickPreset}
            >
              Apply selection
            </button>
            <button
              type="button"
              className="button-secondary"
              onClick={() => setSaveOpen((current) => !current)}
            >
              {saveOpen ? 'Close save' : 'Save current'}
            </button>
            <button type="button" className="button-secondary" onClick={handleReset}>
              Clear all filters
            </button>
          </div>
        </div>
      </div>

      {saveOpen ? (
        <section className="variant-search-section">
          <div className="variant-save-panel">
            <div className="variant-save-panel-row">
              <input
                placeholder="Preset name"
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
              />
              <button
                type="button"
                className="form-button"
                disabled={!presetName.trim() || savingPreset}
                onClick={async () => {
                  try {
                    await onSaveCurrentPreset({
                      name: presetName.trim(),
                      description: presetDescription.trim() || undefined,
                    });
                    setPresetName('');
                    setPresetDescription('');
                    setSaveOpen(false);
                  } catch {
                    // Page-level feedback already shows the error state.
                  }
                }}
              >
                {savingPreset ? 'Saving…' : 'Save'}
              </button>
            </div>
            <details className="variant-saved-disclosure">
              <summary>Add description</summary>
              <textarea
                rows={2}
                placeholder="Optional description"
                value={presetDescription}
                onChange={(event) => setPresetDescription(event.target.value)}
              />
            </details>
          </div>
        </section>
      ) : null}

      {feedback ? (
        <div className={`variant-workspace-feedback variant-workspace-feedback--${feedback.tone}`}>
          {feedback.message}
        </div>
      ) : null}

      {activeFilterChips.length ? (
        <section className="variant-search-section">
          <div className="variant-search-section-copy">
            <p className="analysis-section-title">Active filters</p>
          </div>
          <div className="variant-filter-chip-list">
            {activeFilterChips.map((chip: ActiveSmallFilterChip) => (
              <button
                key={chip.id}
                type="button"
                className="variant-filter-chip"
                onClick={() => removeActiveFilterChip(chip)}
              >
                {getActiveChipLabel(chip)}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <section className="variant-search-section">
        <div className="variant-filter-dropdown-grid">
          <details
            className="variant-filter-dropdown"
            open={openSections.inheritance}
            onToggle={handleSectionToggle('inheritance')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Inheritance</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(inheritanceFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
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
                <label className="variant-summary-select-field">
                  <span>Quality</span>
                  <select
                    aria-label="Quick call quality"
                    value={selectedCallQualityQuickFilter}
                    onChange={(event) => applyCallQualityQuickFilter(event.target.value)}
                  >
                    <option value="all_variants">All</option>
                    <option value="high_quality">High</option>
                    <option value="all_passing">Passing</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
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
                  {carrierScreeningCouple.right.sample_id} carry a variant.
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
                            { value: 'hom-group', label: 'Hom', group: ['1/1', '1|1'] },
                            {
                              value: 'het-group',
                              label: 'Het',
                              group: ['0/1', '1/0', '0|1', '1|0'],
                            },
                            {
                              value: 'ref-group',
                              label: 'WT',
                              group: ['0/0', '0|0', './.', 'absent'],
                            },
                          ].map((option) => (
                            <label key={option.value} className="analysis-checkbox">
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
                        <div className="analysis-filter-grid analysis-filter-grid--4">
                          <input
                            placeholder="GQ / QUAL ≥"
                            value={filter?.qual ?? ''}
                            onChange={(event) =>
                              handleSampleFieldChange(sample, 'qual', event.target.value)
                            }
                          />
                          <input
                            placeholder="DP ≥"
                            value={filter?.dp ?? ''}
                            onChange={(event) =>
                              handleSampleFieldChange(sample, 'dp', event.target.value)
                            }
                          />
                          <input
                            placeholder="AF ≥"
                            value={filter?.af ?? ''}
                            onChange={(event) =>
                              handleSampleFieldChange(sample, 'af', event.target.value)
                            }
                          />
                          <input
                            placeholder="AD alt ≥"
                            value={filter?.ad_alt ?? ''}
                            onChange={(event) =>
                              handleSampleFieldChange(sample, 'ad_alt', event.target.value)
                            }
                          />
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
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.pathogenicity}
            onToggle={handleSectionToggle('pathogenicity')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Pathogenicity</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(pathogenicityFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
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
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
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
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.annotations}
            onToggle={handleSectionToggle('annotations')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Annotations</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(annotationFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>Quick</span>
                  <select
                    aria-label="Quick annotations"
                    value={selectedAnnotationQuickFilter}
                    onChange={(event) => applyAnnotationQuickFilter(event.target.value)}
                  >
                    <option value="all">All</option>
                    <option value="high_impact">High</option>
                    <option value="moderate_to_high">Mod+High</option>
                    <option value="all_coding">Coding</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <div className="variant-annotation-impact-groups">
                {Object.entries(CONSEQUENCE_BY_IMPACT).map(([impact, consequences]) => (
                  <div key={impact} className="variant-annotation-impact-group">
                    <label className="analysis-checkbox variant-annotation-impact-title-row">
                      <input
                        type="checkbox"
                        checked={selectedImpactValues.includes(impact)}
                        onChange={(event) =>
                          handleImpactCategoryToggle(impact, event.target.checked)
                        }
                      />
                      <span className="variant-annotation-impact-title">{impact}</span>
                    </label>
                    <div className="variant-checkbox-grid variant-checkbox-grid--small">
                      {consequences.map((consequence) => (
                        <label
                          key={consequence}
                          className="analysis-checkbox variant-compact-checkbox"
                        >
                          <input
                            type="checkbox"
                            checked={selectedEffectValues.includes(consequence)}
                            onChange={() => toggleDraftFilterListValue('effect', consequence)}
                          />
                          {consequence.replace(/_/g, ' ')}
                        </label>
                      ))}
                    </div>
                  </div>
                ))}
              </div>

              <div className="analysis-filter-grid analysis-filter-grid--4">
                <input
                  name="transcript"
                  placeholder="Transcript"
                  value={draftFilters.transcript}
                  onChange={handleDraftFieldChange}
                />
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

              <div className="variant-gt-toggle-row">
                <label className="analysis-checkbox">
                  <input
                    type="checkbox"
                    checked={draftFilters.canonical_only === 'true'}
                    onChange={(event) =>
                      setDraftFilterValue('canonical_only', event.target.checked ? 'true' : '')
                    }
                  />
                  Canonical only
                </label>
                <label className="analysis-checkbox">
                  <input
                    type="checkbox"
                    checked={draftFilters.mane_only === 'true'}
                    onChange={(event) =>
                      setDraftFilterValue('mane_only', event.target.checked ? 'true' : '')
                    }
                  />
                  MANE only
                </label>
                <label className="analysis-checkbox">
                  <input
                    type="checkbox"
                    checked={draftFilters.lof_only === 'true'}
                    onChange={(event) =>
                      setDraftFilterValue('lof_only', event.target.checked ? 'true' : '')
                    }
                  />
                  LoF only
                </label>
              </div>
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.inSilico}
            onToggle={handleSectionToggle('inSilico')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">In Silico</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(inSilicoFilterCount, 'No filters')}
                </span>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
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
                <input
                  name="min_spliceai"
                  placeholder="SpliceAI ≥"
                  value={draftFilters.min_spliceai}
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
                interpreted as greater-than-or-equal filters.
              </p>
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.frequency}
            onToggle={handleSectionToggle('frequency')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Frequency</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(frequencyFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>Quick</span>
                  <select
                    aria-label="Quick frequency"
                    value={selectedFrequencyQuickFilter}
                    onChange={(event) => applyFrequencyQuickFilter(event.target.value)}
                  >
                    <option value="all">All</option>
                    <option value="gnomad_rare">gnomAD &lt;1%, H/H &lt;=10</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <div className="variant-frequency-slider-grid">
                {[
                  ['max_gnomad_af', 'gnomAD AF'],
                  ['max_gnomad_popmax_af', 'gnomAD popmax AF'],
                  ['max_gnomad_exomes_af', 'gnomAD exomes AF'],
                  ['max_gnomad_genomes_af', 'gnomAD genomes AF'],
                  ['max_topmed_af', 'TOPMed AF'],
                ].map(([key, label]) => (
                  <div key={key} className="variant-frequency-slider-row">
                    <div className="variant-frequency-slider-header">
                      <span>{label}</span>
                      <span>{formatPercentFilterValue(draftFilters[key as keyof SmallFilterState])}</span>
                    </div>
                    <input
                      type="range"
                      min={0}
                      max={10}
                      step={0.1}
                      value={normalizePercentValue(
                        String(Number(draftFilters[key as keyof SmallFilterState] || '0') * 100),
                      )}
                      onChange={(event) =>
                        setFrequencyFromPercent(
                          key as keyof SmallFilterState,
                          Number(event.target.value),
                        )
                      }
                    />
                    <button
                      type="button"
                      className="button-secondary variant-frequency-clear"
                      onClick={() => setDraftFilterValue(key as keyof SmallFilterState, '')}
                    >
                      Clear
                    </button>
                  </div>
                ))}
              </div>
              <div className="analysis-filter-grid analysis-filter-grid--4">
                <input
                  name="max_gnomad_ac"
                  placeholder="gnomAD AC ≤"
                  value={draftFilters.max_gnomad_ac}
                  onChange={handleDraftFieldChange}
                />
                <input
                  name="max_gnomad_hom_count"
                  placeholder="gnomAD H/H ≤"
                  value={draftFilters.max_gnomad_hom_count}
                  onChange={handleDraftFieldChange}
                />
                <input
                  name="max_gnomad_hemi_count"
                  placeholder="gnomAD hemi ≤"
                  value={draftFilters.max_gnomad_hemi_count}
                  onChange={handleDraftFieldChange}
                />
              </div>
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.locations}
            onToggle={handleSectionToggle('locations')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Locations</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(locationFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
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
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <textarea
                name="gene"
                rows={3}
                placeholder="Gene list: BRCA1&#10;BRCA2&#10;TP53"
                value={draftFilters.gene}
                onChange={handleDraftFieldChange}
              />
              <textarea
                name="intervals"
                rows={3}
                placeholder="Intervals: chr13:32315086-32400266&#10;chr17:43044295-43125482"
                value={draftFilters.intervals}
                onChange={handleDraftFieldChange}
              />
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.exclude}
            onToggle={handleSectionToggle('exclude')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Exclude</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(excludeFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>Quick</span>
                  <select
                    aria-label="Quick exclude"
                    value={selectedExcludeQuickFilter}
                    onChange={(event) => applyExcludeQuickFilter(event.target.value)}
                  >
                    <option value="all">None</option>
                    <option value="benign_likely_benign">Benign/Likely benign</option>
                    <option value="excluded_tag">Excluded tag</option>
                    <option value="excluded_and_benign">Excluded + benign</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
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
                  {customTagOptions.length ? (
                    <div className="variant-checkbox-grid variant-checkbox-grid--small">
                      {customTagOptions.map((option) => (
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
              <textarea
                name="exclude_gene"
                rows={3}
                placeholder="Excluded genes: TTN&#10;MUC4"
                value={draftFilters.exclude_gene}
                onChange={handleDraftFieldChange}
              />
              <textarea
                name="exclude_intervals"
                rows={3}
                placeholder="Excluded intervals: chr1:1000-5000"
                value={draftFilters.exclude_intervals}
                onChange={handleDraftFieldChange}
              />
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.review}
            onToggle={handleSectionToggle('review')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Review and curation</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(reviewFilterCount, 'No filters')}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>Quick</span>
                  <select
                    aria-label="Quick review"
                    value={selectedReviewQuickFilter}
                    onChange={(event) => applyReviewQuickFilter(event.target.value)}
                  >
                    <option value="all">All</option>
                    <option value="pathogenic_vus">P/LP/VUS</option>
                    <option value="review_tag">Review tag</option>
                    <option value="custom">Custom</option>
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">
                ▾
              </span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <div className="variant-review-curation-columns">
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
                  {customTagOptions.length ? (
                    <div className="variant-checkbox-grid variant-checkbox-grid--small">
                      {customTagOptions.map((option) => (
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

            </div>
          </details>
        </div>
      </section>

      <div className="variant-search-actions">
        <button type="submit" className="form-button">
          Apply filters
        </button>
      </div>
    </form>
  );
};

export default SmallVariantFilterForm;
