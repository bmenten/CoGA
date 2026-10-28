import { useId, useState } from 'react';
import type { ChangeEvent, SyntheticEvent } from 'react';
import {
  ALL_GT_GROUPS,
  BUILT_IN_SMALL_PRESETS,
  addableTagDefinitions,
  buildSmallVariantQueryParams,
  heldTagOptions,
  sortTagDefinitions,
  resolveCarrierScreeningCoupleMembers,
  type ActiveSmallFilterChip,
  type FamilyMember,
  type GenePanel,
  type LocationProblems,
  type SmallFilterState,
  type SmallVariantFilterPreset,
  type SmallVariantSampleFilter,
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
  SECTION_FILTER_COUNTS,
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
import { reviewTagLabel } from '../../lib/reviewTags';

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
   * Explorer, #526; the NIPT page; the family small-variant page, whose list applies none of
   * the NIPT list's own filters). Defaults to none.
   */
  unsupportedFilters?: ReadonlySet<keyof SmallFilterState>;
  /**
   * Said once under the active filters: which filters of the search (from a saved search or
   * a link) the list does not apply, and so has no chip for (the NIPT page, ENG-19; the family
   * small-variant page, for a search saved on the NIPT page, found while fixing #1012).
   */
  unappliedFiltersNote?: string | null;
  /**
   * Monogenic NIPT mode. When 'nipt', the genotype/inheritance subsection is
   * replaced by a maternal/fetal Categories subsection. The NIPT page passes the review
   * filters its list does not apply as `unsupportedFilters` (NIPT_UNSUPPORTED_FILTERS):
   * its review subsection offers the review tags only. Defaults to 'small-variant' so the
   * small-variant and global-explorer pages are untouched.
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
  /**
   * The preset the page opened on (the family small-variant page's fresh-open default),
   * named as the active filter until another is picked or the filters are cleared. Pass it
   * once the draft holds it; `overrides` are what the page set on top of the preset (the
   * Mendeliome panel), so "Reset to preset" restores the same draft.
   */
  initialPreset?: {
    value: string;
    label: string;
    overrides?: Partial<SmallFilterState>;
  } | null;
  /**
   * When true, the filter categories sit behind an "Advanced filters" toggle, collapsed on
   * open. The fields stay mounted while collapsed, so collapsing never changes a filter.
   */
  collapsibleAdvanced?: boolean;
};

/** The preset or saved search the draft was loaded from, and the draft it loaded. */
type LoadedPreset = {
  value: string;
  label: string;
  isDefault: boolean;
  overrides?: Partial<SmallFilterState>;
  /** The draft as the preset left it (draftKey); null until the preset's draft has landed. */
  baseline: string | null;
  /** The draft object the preset replaces: the baseline is taken once the draft differs. */
  pendingFrom: SmallFilterState | null;
};

/**
 * The draft as a comparable key: its query parameters, sorted, so the order values were
 * ticked in does not count as a change. Only compared, never sent.
 */
const draftKey = (
  filters: SmallFilterState,
  sampleFilters: Record<string, SmallVariantSampleFilter>,
) => {
  const params = buildSmallVariantQueryParams(filters, sampleFilters, 1);
  return Array.from(params.entries())
    .map(([key, value]) => {
      if (key !== 'sample_filter') return `${key}=${value}`;
      // sample:gt|gt…:qual:dp:af:ad_alt — the genotypes in the order they were ticked.
      const [sample, gt = '', ...thresholds] = value.split(':');
      return `${key}=${[sample, gt.split('|').sort().join('|'), ...thresholds].join(':')}`;
    })
    .sort()
    .join('&');
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
  unappliedFiltersNote = null,
  mode = 'small-variant',
  categoryCounts = {},
  draftLocationProblems = null,
  categoryLabels,
  niptInheritancePresets,
  builtInPresets = BUILT_IN_SMALL_PRESETS,
  initialPreset = null,
  collapsibleAdvanced = false,
}: SmallVariantFilterFormProps) => {
  const offers = (key: keyof SmallFilterState) => !unsupportedFilters?.has(key);
  const [selectedQuickPreset, setSelectedQuickPreset] = useState('');
  const [saveOpen, setSaveOpen] = useState(false);
  const [openSections, setOpenSections] = useState<OpenSections>(DEFAULT_OPEN_SECTIONS);
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  const [advancedOpen, setAdvancedOpen] = useState(!collapsibleAdvanced);
  const advancedPanelId = useId();
  const [loadedPreset, setLoadedPreset] = useState<LoadedPreset | null>(null);
  const [initialPresetTaken, setInitialPresetTaken] = useState(false);
  const currentDraftKey = draftKey(draftFilters, sampleDraftFilters);
  // The page's opening preset is named once its draft is in place; a pick before then wins.
  if (initialPreset && !initialPresetTaken) {
    setInitialPresetTaken(true);
    setSelectedQuickPreset(initialPreset.value);
    setLoadedPreset({
      value: initialPreset.value,
      label: initialPreset.label,
      isDefault: true,
      overrides: initialPreset.overrides,
      baseline: currentDraftKey,
      pendingFrom: null,
    });
  }
  // A picked preset lands in the draft with this render: take that draft as its baseline.
  if (loadedPreset && loadedPreset.baseline === null && draftFilters !== loadedPreset.pendingFrom) {
    setLoadedPreset({ ...loadedPreset, baseline: currentDraftKey, pendingFrom: null });
  }
  const presetModified =
    loadedPreset !== null &&
    loadedPreset.baseline !== null &&
    loadedPreset.baseline !== currentDraftKey;
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
    // A deleted tag reads "(deleted)", as on the other chips (`reviewTagLabel`; ENG-10).
    if (chip.kind === 'top' && chip.key === 'review_tags' && chip.value) {
      const tag = tags.find((entry) => entry.key === chip.value);
      return `Review tags: ${reviewTagLabel(tag, chip.value)}`;
    }
    if (chip.kind === 'top' && chip.key === 'clinvar' && chip.value) {
      return `Pathogenicity: ${chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'exclude_clinvar' && chip.value) {
      return `Exclude pathogenicity: ${chip.value}`;
    }
    if (chip.kind === 'top' && chip.key === 'exclude_review_tags' && chip.value) {
      const tag = tags.find((entry) => entry.key === chip.value);
      return `Exclude review tags: ${reviewTagLabel(tag, chip.value)}`;
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
  const loadPreset = (value: string, overrides?: Partial<SmallFilterState>) => {
    if (value.startsWith('built-in:')) {
      applyPreset(value.replace('built-in:', '') as SmallPreset);
    } else {
      const preset = presets.find((entry) => `saved:${entry._id}` === value);
      if (!preset) return false;
      applySavedPreset(preset);
    }
    Object.entries(overrides ?? {}).forEach(([key, overrideValue]) => {
      setDraftFilterValue(key as keyof SmallFilterState, overrideValue ?? '');
    });
    return true;
  };

  const presetLabel = (value: string) => {
    if (value.startsWith('built-in:')) {
      const id = value.replace('built-in:', '');
      return builtInPresets.find((preset) => preset.value === id)?.label ?? id;
    }
    return presets.find((entry) => `saved:${entry._id}` === value)?.name ?? value;
  };

  const populateFromQuickPreset = (value: string) => {
    setSelectedQuickPreset(value);
    setInitialPresetTaken(true);
    if (!value) {
      setLoadedPreset(null);
      return;
    }
    // Both apply functions do nothing without family members: then nothing was loaded.
    if (members.length && loadPreset(value)) {
      setLoadedPreset({
        value,
        label: presetLabel(value),
        isDefault: false,
        baseline: null,
        pendingFrom: draftFilters,
      });
    } else {
      setLoadedPreset(null);
    }
  };

  // Put the loaded preset's draft back (the preset loaded again, as picking it does).
  const resetToLoadedPreset = () => {
    if (loadedPreset) loadPreset(loadedPreset.value, loadedPreset.overrides);
  };
  const canResetToPreset =
    loadedPreset !== null &&
    (loadedPreset.value.startsWith('built-in:') ||
      presets.some((entry) => `saved:${entry._id}` === loadedPreset.value));

  const clearAllFilters = () => {
    setSelectedQuickPreset('');
    setLoadedPreset(null);
    setInitialPresetTaken(true);
    handleReset();
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

  // The categories the form shows, as rendered below, and how many have a filter set.
  const visibleSections = (Object.keys(SECTION_FILTER_COUNTS) as (keyof OpenSections)[]).filter(
    (section) => {
      if (section === 'phenotype' || section === 'svSecondHit' || section === 'inheritance') {
        return familyAware;
      }
      if (section === 'categories') return mode === 'nipt';
      return true;
    },
  );
  const sectionsWithFilters = visibleSections.filter(
    (section) => SECTION_FILTER_COUNTS[section](form) > 0,
  ).length;

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
                  className={`variant-preset-select${
                    loadedPreset ? ' variant-preset-select--active' : ''
                  }${presetModified ? ' variant-preset-select--modified' : ''}`}
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
            <button type="button" className="button-secondary" onClick={clearAllFilters}>
              Clear all filters
            </button>
          </div>
          {familyAware || mode === 'nipt' ? (
            <p
              className={`variant-active-preset${loadedPreset ? '' : ' variant-active-preset--none'}`}
              role="status"
              aria-live="polite"
            >
              <span className="variant-active-preset-key">Active filter:</span>{' '}
              {loadedPreset ? (
                <>
                  <strong className="variant-active-preset-name">{loadedPreset.label}</strong>
                  {loadedPreset.isDefault ? (
                    <span className="variant-active-preset-tag">default</span>
                  ) : null}
                  {presetModified ? (
                    <span className="variant-active-preset-tag variant-active-preset-tag--modified">
                      modified
                    </span>
                  ) : null}
                  {presetModified && canResetToPreset ? (
                    <button
                      type="button"
                      className="button-link variant-active-preset-reset"
                      onClick={resetToLoadedPreset}
                    >
                      Reset to preset
                    </button>
                  ) : null}
                </>
              ) : (
                <span className="variant-active-preset-name">custom (no preset loaded)</span>
              )}
            </p>
          ) : null}
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

      {activeFilterChips.length || unappliedFiltersNote ? (
        <section className="variant-search-section">
          <div className="variant-search-section-copy">
            <p className="analysis-section-title">Active filters</p>
          </div>
          {activeFilterChips.length ? (
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
          ) : null}
          {unappliedFiltersNote ? (
            <p className="status-note status-note--warning" role="note">
              {unappliedFiltersNote}
            </p>
          ) : null}
        </section>
      ) : null}

      <section className="variant-search-section">
        {collapsibleAdvanced ? (
          <button
            type="button"
            className="variant-advanced-toggle"
            aria-expanded={advancedOpen}
            aria-controls={advancedPanelId}
            onClick={() => setAdvancedOpen((current) => !current)}
          >
            <span className="variant-filter-dropdown-caret" aria-hidden="true">
              ▾
            </span>
            <span className="variant-advanced-toggle-title">Advanced filters</span>
            <span className="variant-advanced-toggle-meta">
              ({visibleSections.length} categories
              {sectionsWithFilters > 0 ? `, ${sectionsWithFilters} with filters set` : ''})
            </span>
          </button>
        ) : null}
        {/* Hidden, not unmounted: collapsing keeps every field and its value as it is. */}
        <div
          id={collapsibleAdvanced ? advancedPanelId : undefined}
          className="variant-filter-dropdown-grid"
          hidden={collapsibleAdvanced && !advancedOpen}
        >
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

          <ReviewFilterSection form={form} />
        </div>
      </section>
    </form>
  );
};

export default SmallVariantFilterForm;
