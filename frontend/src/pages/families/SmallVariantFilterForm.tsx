import { useState } from 'react';
import type { ChangeEvent, SyntheticEvent } from 'react';
import {
  ALL_GT_GROUPS,
  BUILT_IN_SMALL_PRESETS,
  addableTagDefinitions,
  heldTagOptions,
  sortTagDefinitions,
  resolveCarrierScreeningCoupleMembers,
  type ActiveSmallFilterChip,
  type FamilyMember,
  type GenePanel,
  type LocationProblems,
  type SmallFilterState,
  type SmallVariantFilterPreset,
  type SmallPreset,
  type SmallVariantSearchState,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  DEFAULT_OPEN_SECTIONS,
  GT_GROUP_OPTIONS,
  type FilterSectionForm,
  type GtGroupId,
  type OpenSections,
  PhenotypeFilterSection,
  SvSecondHitFilterSection,
  InheritanceFilterSection,
  NiptCategoriesFilterSection,
  PathogenicityFilterSection,
  AnnotationsFilterSection,
  InSilicoFilterSection,
  FrequencyFilterSection,
  LocationsFilterSection,
  ExcludeFilterSection,
  ReviewFilterSection,
} from './smallVariantFilterSections';

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
  | 'relationships'
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
  /**
   * When false, family/sample-specific controls (inheritance, per-sample
   * genotype/QC, pedigree-derived presets) are hidden. Used by the family-
   * agnostic Global Small Variant Explorer. Defaults to true.
   */
  familyAware?: boolean;
  /**
   * Filters the search behind this form does not apply. They get no control, so the
   * form never offers a filter the results would not reflect (the Global Small Variant
   * Explorer, #526). Defaults to none.
   */
  unsupportedFilters?: ReadonlySet<keyof SmallFilterState>;
  /**
   * Monogenic NIPT mode. When 'nipt', the genotype/inheritance subsection is
   * replaced by a maternal/fetal Categories subsection and the review-state
   * subsection is hidden (it does not apply to inferred cfDNA calls). Defaults
   * to 'small-variant' so the small-variant and global-explorer pages are
   * untouched.
   */
  mode?: 'small-variant' | 'nipt';
  /** NIPT: per-category variant counts, shown next to each category option; null when
   * they could not be loaded, so they read as unknown rather than 0 (#606). */
  categoryCounts?: Record<string, number> | null;
  /** Draft location filters that cannot be read, named under their fields (#604). */
  draftLocationProblems?: LocationProblems | null;
  /** NIPT: labels for the eight maternal/fetal categories. */
  categoryLabels?: Record<number, string>;
  /**
   * NIPT: inheritance presets (de novo / paternal dominant / …). `categories` is
   * the comma-joined category list to check when the preset is picked (de novo →
   * '1', paternal dominant → '7', maternal dominant → '3'); omit it to leave the
   * category checkboxes untouched, or pass '' to clear them.
   */
  niptInheritancePresets?: { value: string; label: string; categories?: string }[];
  /**
   * Built-in quick presets shown in the toolbar. Defaults to the small-variant
   * built-ins; the NIPT page passes its own (de novo / recessive).
   */
  builtInPresets?: typeof BUILT_IN_SMALL_PRESETS;
};

const CLINVAR_OPTIONS = [
  'Pathogenic',
  'Likely pathogenic',
  'Uncertain significance',
  'Likely benign',
  'Benign',
  'Conflicting classifications',
] as const;

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
  relationships,
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
  familyAware = true,
  unsupportedFilters,
  mode = 'small-variant',
  categoryCounts = {},
  draftLocationProblems = null,
  categoryLabels,
  niptInheritancePresets,
  builtInPresets = BUILT_IN_SMALL_PRESETS,
}: SmallVariantFilterFormProps) => {
  const offers = (key: keyof SmallFilterState) => !unsupportedFilters?.has(key);
  const [selectedQuickPreset, setSelectedQuickPreset] = useState('');
  const [saveOpen, setSaveOpen] = useState(false);
  const [openSections, setOpenSections] = useState<OpenSections>(DEFAULT_OPEN_SECTIONS);
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  const carrierScreeningCouple = resolveCarrierScreeningCoupleMembers(members, relationships);
  const availableBuiltInPresets = builtInPresets.filter(
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

  const clinvarOptions = CLINVAR_OPTIONS.map((option) => ({
    value: option,
    label: option,
  }));
  const sortedTagDefinitions = sortTagDefinitions(addableTagDefinitions(tags));
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
  // A deleted tag a preset or a link still filters on is offered nowhere: it is listed where
  // it is selected, marked, so it can be unticked.
  const selectedHeldTagOptions = (selected: string[]) =>
    heldTagOptions(
      selected,
      tags,
      sortedTagDefinitions.map((tag) => tag.key),
    );

  const summarizeSection = (count: number, emptyLabel: string) =>
    count > 0 ? `${count} active` : emptyLabel;

  const toggleSingleValueCheckbox = (key: keyof SmallFilterState, optionValue: string) => {
    setDraftFilterValue(key, draftFilters[key] === optionValue ? '' : optionValue);
  };

  const numericFilterEquals = (value: string, target: string) => {
    if (!value.trim()) return false;
    return Number(value) === Number(target);
  };

  // Selecting a preset populates the draft filters immediately (chips + sample
  // thresholds update) so the user can review/tweak before hitting Apply. The
  // single "Apply filters" button is what actually runs the search.
  const populateFromQuickPreset = (value: string) => {
    setSelectedQuickPreset(value);
    if (!value) return;
    if (value.startsWith('built-in:')) {
      applyPreset(value.replace('built-in:', '') as SmallPreset);
      return;
    }
    const preset = presets.find((entry) => `saved:${entry._id}` === value);
    if (preset) {
      applySavedPreset(preset);
    }
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

  // What the section components read (#528).
  const form: FilterSectionForm = {
    activeSampleMemberCount,
    carrierScreeningCouple,
    categoryCounts,
    categoryLabels,
    clinvarOptions,
    customTagOptions,
    draftFilters,
    draftLocationProblems,
    handleDraftFieldChange,
    handleGtToggle,
    handleSampleFieldChange,
    handleSectionToggle,
    members,
    niptInheritancePresets,
    numericFilterEquals,
    offers,
    openSections,
    panels,
    sampleDraftFilters,
    selectedHeldTagOptions,
    setDraftFilterValue,
    setSampleQualityThresholds,
    standardTagOptions,
    summarizeSection,
    toggleDraftFilterListValue,
    toggleSingleValueCheckbox,
    updateInheritanceGenotypes,
  };

  return (
    <form onSubmit={handleApply} className="space-y-4 variant-search-workspace">
      <div className="variant-search-header">
        <div className="variant-search-meta">
          <div className="variant-search-toolbar">
            <button type="submit" className="form-button">
              Apply filters
            </button>
            {familyAware || mode === 'nipt' ? (
              <>
                <select
                  aria-label="Preset or saved search"
                  value={selectedQuickPreset}
                  onChange={(event) => populateFromQuickPreset(event.target.value)}
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
                  onClick={() => setSaveOpen((current) => !current)}
                >
                  {saveOpen ? 'Close save' : 'Save current'}
                </button>
              </>
            ) : null}
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
                className="badge-chip variant-filter-chip"
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
          {familyAware ? (
          <PhenotypeFilterSection form={form} />
          ) : null}
          {familyAware ? (
          <SvSecondHitFilterSection form={form} />
          ) : null}
          {familyAware ? (
          <InheritanceFilterSection form={form} />
          ) : null}

          {mode === 'nipt' ? (
          <NiptCategoriesFilterSection form={form} />
          ) : null}

          <PathogenicityFilterSection form={form} />

          <AnnotationsFilterSection form={form} />

          <InSilicoFilterSection form={form} />

          <FrequencyFilterSection form={form} />

          <LocationsFilterSection form={form} />

          <ExcludeFilterSection form={form} />

          {mode !== 'nipt' && (
          <ReviewFilterSection form={form} />
          )}
        </div>
      </section>
    </form>
  );
};

export default SmallVariantFilterForm;
