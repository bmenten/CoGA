import { useState } from 'react';
import type { ChangeEvent, SyntheticEvent } from 'react';
import type {
  ActiveStructuralFilterChip,
  StructuralFilterState,
  StructuralVariantFilterPreset,
  StructuralGenePanel,
  StructuralPreset,
  StructuralVariantTagDefinition,
  StructuralVariantSearchState,
} from './structuralVariantSearch';
import {
  getPresetScopeLabel,
  STRUCTURAL_ALL_GT_GROUPS,
  STRUCTURAL_HET_GT_GROUP,
  STRUCTURAL_HOM_GT_GROUP,
  STRUCTURAL_REF_GT_GROUP,
} from './structuralVariantSearch';
import {
  ACMG_CLASSIFICATION_TAGS,
  addableTagDefinitions,
  countPresetRules,
  heldTagOptions,
  sortTagDefinitions,
} from './smallVariantSearch';
import { GENOTYPE_GROUP_HINTS } from '../../lib/genotypes';
import { parseCommaSeparatedValues } from '../../lib/sampleFilterState';
import {
  countNonEmpty,
  formatPercentFilterValue,
  normalizePercentValue,
} from './variantFilterFormUtils';
import { FilterAccordion, FilterSection } from './VariantFilterAccordion';
import RankByField from './RankByField';

type StructuralVariantFilterFormProps = Pick<
  StructuralVariantSearchState,
  | 'activeFilterChips'
  | 'applyPreset'
  | 'draftFilters'
  | 'draftLocationProblem'
  | 'handleGtToggle'
  | 'handleReset'
  | 'handleSampleFieldChange'
  | 'handleSearch'
  | 'orderedMembers'
  | 'removeActiveFilterChip'
  | 'sampleDraftFilters'
  | 'setDraftFilterValue'
  | 'toggleDraftFilterListValue'
> & {
  applySavedPreset: (preset: StructuralVariantFilterPreset) => void;
  /** Delete one of the user's saved searches; without it, no delete is offered. */
  deletingPreset?: boolean;
  feedback?: { type: 'success' | 'error'; message: string } | null;
  onDeletePreset?: (preset: StructuralVariantFilterPreset) => void | Promise<void>;
  onSaveCurrentPreset: (payload: { name: string; description?: string; scope: 'family' | 'global' }) => void | Promise<void>;
  panels: StructuralGenePanel[];
  presets: StructuralVariantFilterPreset[];
  savingPreset?: boolean;
  tags: StructuralVariantTagDefinition[];
};

const PRESETS: { value: StructuralPreset; label: string }[] = [
  { value: 'mendeliome', label: 'Mendeliome' },
  { value: 'dominant', label: 'Dominant' },
  { value: 'recessive', label: 'Recessive-like' },
  { value: 'any_affected', label: 'Any affected' },
];

const SV_TYPE_OPTIONS = [
  { value: 'DEL', label: 'DEL' },
  { value: 'DUP', label: 'DUP' },
  { value: 'INS', label: 'INS' },
  { value: 'INV', label: 'INV' },
  { value: 'CNV', label: 'CNV' },
  { value: 'BND', label: 'BND' },
  { value: 'TRA', label: 'TRA' },
] as const;

const REGION_FLAG_OPTIONS = [
  'CDS',
  'UTR',
  'ORegAnno',
  'TRE',
  'Centromeric',
  'Pericentromeric',
  'Telomeric',
  'Segdup',
  'Repeat',
  'Gap',
  'Homopolymer',
  'HiConf',
] as const;

// The filter categories, each closed on open.
const CLOSED_SECTIONS = {
  phenotype: false,
  support: false,
  locations: false,
  classAndBreakpoints: false,
  needlr: false,
  review: false,
};

const StructuralVariantFilterForm = ({
  activeFilterChips,
  applyPreset,
  draftFilters,
  draftLocationProblem,
  handleGtToggle,
  handleReset,
  handleSampleFieldChange,
  handleSearch,
  orderedMembers,
  applySavedPreset,
  deletingPreset = false,
  feedback,
  onDeletePreset,
  onSaveCurrentPreset,
  panels,
  presets,
  removeActiveFilterChip,
  sampleDraftFilters,
  savingPreset = false,
  setDraftFilterValue,
  tags,
  toggleDraftFilterListValue,
}: StructuralVariantFilterFormProps) => {
  const [openSections, setOpenSections] = useState(CLOSED_SECTIONS);
  const [saveOpen, setSaveOpen] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');
  // A saved search is for this family, or reusable: for every family the user opens.
  const [presetScope, setPresetScope] = useState<StructuralVariantFilterPreset['scope']>('family');
  const selectedSavedPreset = selectedPreset.startsWith('saved:')
    ? presets.find((entry) => `saved:${entry._id}` === selectedPreset)
    : undefined;

  const saveCurrentPreset = async () => {
    try {
      await onSaveCurrentPreset({
        name: presetName.trim(),
        description: presetDescription.trim() || undefined,
        scope: presetScope,
      });
      setPresetName('');
      setPresetDescription('');
      setPresetScope('family');
      setSaveOpen(false);
    } catch {
      // The page says why; the name and description stay, to try again.
    }
  };

  const deleteSelectedPreset = async () => {
    if (!selectedSavedPreset || !onDeletePreset) return;
    const confirmed = window.confirm(
      selectedSavedPreset.scope === 'global'
        ? `Delete the saved search "${selectedSavedPreset.name}"? It is reusable: it goes from every family.`
        : `Delete the saved search "${selectedSavedPreset.name}" of this family?`,
    );
    if (!confirmed) return;
    try {
      await onDeletePreset(selectedSavedPreset);
      setSelectedPreset('');
    } catch {
      // The page says why; the saved search stays selected.
    }
  };

  const handleDraftFieldChange = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setDraftFilterValue(event.target.name as keyof StructuralFilterState, event.target.value);
  };

  const handleSectionToggle =
    (section: keyof typeof openSections) => (event: SyntheticEvent<HTMLDetailsElement>) => {
      const nextOpen = event.currentTarget.open;
      setOpenSections((prev) => ({ ...prev, [section]: nextOpen }));
    };

  // "Expand all" / "Collapse all": every category open, or every one closed.
  const setAllSections = (open: boolean) => {
    setOpenSections(
      Object.fromEntries(
        Object.keys(CLOSED_SECTIONS).map((section) => [section, open]),
      ) as typeof CLOSED_SECTIONS,
    );
  };

  const getActiveChipLabel = (chip: ActiveStructuralFilterChip) => {
    if (chip.kind === 'top' && chip.key === 'panel_id') {
      // The applied panel, as the chip's filter is: the draft's panel named a panel the shown
      // results were not filtered by, until the form was applied.
      const panel = panels.find((entry) => entry._id === chip.value);
      return `Gene panel: ${panel?.name || chip.value}`;
    }
    return chip.label;
  };

  const sampleFilterCount = orderedMembers.reduce((count, member) => {
    const filter = sampleDraftFilters[member.sample_id];
    if (!filter) return count;
    const genotypeActive = filter.gt.length > 0 && filter.gt.length < STRUCTURAL_ALL_GT_GROUPS.length;
    const thresholdActive = Boolean(filter.qual || filter.read_support || filter.filter);
    return count + (genotypeActive || thresholdActive ? 1 : 0);
  }, 0);
  const phenotypeFilterCount = draftFilters.prioritize === 'true' ? 1 : 0;
  const supportFilterCount = sampleFilterCount + countNonEmpty(draftFilters.inheritance);
  const locationFilterCount = countNonEmpty(
    draftFilters.locus,
    draftFilters.panel_id,
    draftFilters.gene,
    draftFilters.chr,
    draftFilters.start,
    draftFilters.end,
  );
  const classFilterCount =
    parseCommaSeparatedValues(draftFilters.type).length +
    countNonEmpty(
      draftFilters.source,
      draftFilters.minLength,
      draftFilters.length,
      draftFilters.remote_chr,
      draftFilters.remote_start,
    );
  const needlrFilterCount =
    countNonEmpty(
      draftFilters.phenotype,
      draftFilters.hpo,
      draftFilters.moi,
      draftFilters.gencc_support,
      draftFilters.max_control_af,
      draftFilters.max_population_af,
      draftFilters.min_pli,
    ) + parseCommaSeparatedValues(draftFilters.region_flags).length;
  const reviewFilterCount =
    parseCommaSeparatedValues(draftFilters.classification).length +
    parseCommaSeparatedValues(draftFilters.review_tags).length +
    parseCommaSeparatedValues(draftFilters.exclude_review_tags).length +
    (draftFilters.has_notes === 'true' ? 1 : 0);
  // One count per category, as the accordion lists them.
  const sectionFilterCounts = [
    phenotypeFilterCount,
    supportFilterCount,
    locationFilterCount,
    classFilterCount,
    needlrFilterCount,
    reviewFilterCount,
  ];
  const sortedTags = sortTagDefinitions(addableTagDefinitions(tags));
  const nonClassificationTags = sortedTags.filter(
    (tag) => !ACMG_CLASSIFICATION_TAGS.some((option) => option.key === tag.key),
  );
  // The tags offered, then any selected one they leave out (a deleted tag a preset or a
  // link still filters on), marked, so it can be unticked.
  const tagOptionsWith = (selected: string) => [
    ...nonClassificationTags.map((tag) => ({ value: tag.key, label: tag.label })),
    ...heldTagOptions(
      parseCommaSeparatedValues(selected),
      tags,
      nonClassificationTags.map((tag) => tag.key),
    ),
  ];
  const reviewTagOptions = tagOptionsWith(draftFilters.review_tags);
  const excludeReviewTagOptions = tagOptionsWith(draftFilters.exclude_review_tags);

  const summarizeSection = (count: number, emptyLabel = 'No filters') =>
    count > 0 ? `${count} active` : emptyLabel;

  const setFrequencyFromPercent = (
    key: 'max_control_af' | 'max_population_af',
    percentValue: number,
  ) => {
    if (!Number.isFinite(percentValue) || percentValue <= 0) {
      setDraftFilterValue(key, '');
      return;
    }
    const normalized = Math.min(10, Math.max(0, percentValue)) / 100;
    setDraftFilterValue(key, normalized.toFixed(4).replace(/\.?0+$/, ''));
  };

  return (
    <form id="sv-filters" className="space-y-4 variant-search-workspace" onSubmit={handleSearch}>
      <div className="variant-search-header">
        <div className="variant-search-meta">
          <div className="variant-search-toolbar">
            <button type="submit" className="form-button">
              Apply filters
            </button>
            <select
              className="variant-saved-filter-select"
              aria-label="Preset or saved search"
              value={selectedPreset}
              onChange={(event) => {
                const value = event.target.value;
                setSelectedPreset(value);
                if (!value) return;
                // Selecting a preset/saved search populates the draft; the single
                // "Apply filters" button runs the search.
                if (value.startsWith('built-in:')) {
                  applyPreset(value.replace('built-in:', '') as StructuralPreset);
                  return;
                }
                const preset = presets.find((entry) => `saved:${entry._id}` === value);
                if (preset) applySavedPreset(preset);
              }}
            >
              <option value="">Preset or saved search</option>
              <optgroup label="Inheritance presets (rare, &lt;1% population AF)">
                {PRESETS.map((preset) => (
                  <option key={preset.value} value={`built-in:${preset.value}`}>
                    {preset.label}
                  </option>
                ))}
              </optgroup>
              {presets.length ? (
                <optgroup label="Saved searches">
                  {presets.map((preset) => (
                    <option key={preset._id} value={`saved:${preset._id}`}>
                      {preset.name} ({getPresetScopeLabel(preset.scope)}, {countPresetRules(preset)})
                    </option>
                  ))}
                </optgroup>
              ) : null}
            </select>
            <button type="button" className="button-secondary" onClick={handleReset}>
              Clear all filters
            </button>
            <button type="button" className="button-secondary" onClick={() => setSaveOpen((value) => !value)}>
              Save current
            </button>
            {selectedSavedPreset && onDeletePreset ? (
              <button
                type="button"
                className="button-secondary"
                onClick={() => void deleteSelectedPreset()}
                disabled={deletingPreset}
              >
                {deletingPreset ? 'Deleting…' : 'Delete saved search'}
              </button>
            ) : null}
          </div>
        </div>
      </div>

      {saveOpen ? (
        <section className="variant-search-section">
          <div className="variant-save-panel">
            <div className="variant-save-panel-row">
              <input
                placeholder="Filter name"
                value={presetName}
                onChange={(event) => setPresetName(event.target.value)}
              />
              <button
                type="button"
                className="form-button"
                disabled={!presetName.trim() || savingPreset}
                onClick={() => void saveCurrentPreset()}
              >
                {savingPreset ? 'Saving...' : 'Save'}
              </button>
            </div>
            <input
              placeholder="Description"
              value={presetDescription}
              onChange={(event) => setPresetDescription(event.target.value)}
            />
            <fieldset className="variant-save-scope">
              <legend>Use it for</legend>
              <label>
                <input
                  type="radio"
                  name="sv-preset-scope"
                  value="family"
                  checked={presetScope === 'family'}
                  onChange={() => setPresetScope('family')}
                />
                This family
              </label>
              <label>
                <input
                  type="radio"
                  name="sv-preset-scope"
                  value="global"
                  checked={presetScope === 'global'}
                  onChange={() => setPresetScope('global')}
                />
                Every family I open (reusable)
              </label>
            </fieldset>
          </div>
        </section>
      ) : null}

      {feedback ? (
        <div className={`variant-workspace-feedback variant-workspace-feedback--${feedback.type}`}>
          {feedback.message}
        </div>
      ) : null}

      {activeFilterChips.length ? (
        <section className="variant-search-section">
          <div className="variant-search-section-copy">
            <p className="analysis-section-title">Active filters</p>
          </div>
          <div className="variant-filter-chip-list">
            {activeFilterChips.map((chip) => (
              <button
                key={chip.id}
                type="button"
                className="badge-chip variant-filter-chip"
                onClick={() => removeActiveFilterChip(chip)}
                title="Remove filter"
              >
                {getActiveChipLabel(chip)}
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <FilterAccordion
        collapsible
        categoryCount={sectionFilterCounts.length}
        activeCategoryCount={sectionFilterCounts.filter((count) => count > 0).length}
        onExpandAll={() => setAllSections(true)}
        onCollapseAll={() => setAllSections(false)}
      >
        <FilterSection
          title="Phenotype"
          open={openSections.phenotype}
          onToggle={handleSectionToggle('phenotype')}
          active={phenotypeFilterCount > 0}
          meta={summarizeSection(phenotypeFilterCount, 'Prioritization off')}
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
              Rank structural variants by how well their overlapped gene(s) match the affected
              individuals’ HPO phenotypes (Monarch / Exomiser-style), blended with event class,
              gene constraint, rarity, and segregation.
            </p>
            <RankByField
              prioritize={draftFilters.prioritize === 'true'}
              rankBy={draftFilters.rank_by}
              onChange={(value) => setDraftFilterValue('rank_by', value)}
            />
          </div>
        </FilterSection>

        <FilterSection
          title="Inheritance and support"
          open={openSections.support}
          onToggle={handleSectionToggle('support')}
          active={supportFilterCount > 0}
          meta={summarizeSection(supportFilterCount)}
          controls={
            <>
              <label className="variant-summary-select-field">
                <span>Inheritance</span>
                <select
                  name="inheritance"
                  value={draftFilters.inheritance}
                  onChange={handleDraftFieldChange}
                >
                  <option value="">Any</option>
                  <option value="de_novo">De novo</option>
                  <option value="maternal">Maternal</option>
                  <option value="paternal">Paternal</option>
                  <option value="inherited">Inherited</option>
                </select>
              </label>
            </>
          }
        >
          <div className="variant-filter-dropdown-content">
            <div className="variant-sample-grid">
              {orderedMembers.map((member) => {
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
                        <span className={`table-chip ${member.affected ? 'badge-chip--signature' : ''}`}>
                          {member.affected ? 'affected' : 'unaffected'}
                        </span>
                      </div>
                    </div>
                    <div className="variant-sample-controls">
                      <div className="variant-gt-toggle-row">
                        {[
                          { value: 'hom-group', label: 'Hom', group: STRUCTURAL_HOM_GT_GROUP },
                          { value: 'het-group', label: 'Het', group: STRUCTURAL_HET_GT_GROUP },
                          { value: 'ref-group', label: 'WT', group: STRUCTURAL_REF_GT_GROUP },
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
                      <div className="analysis-filter-grid analysis-filter-grid--3">
                        <input
                          placeholder="QUAL ≥"
                          value={filter?.qual ?? ''}
                          onChange={(event) =>
                            handleSampleFieldChange(sample, 'qual', event.target.value)
                          }
                        />
                        <input
                          placeholder="Read support ≥"
                          value={filter?.read_support ?? ''}
                          onChange={(event) =>
                            handleSampleFieldChange(sample, 'read_support', event.target.value)
                          }
                        />
                        <input
                          placeholder="Filter text"
                          value={filter?.filter ?? ''}
                          onChange={(event) =>
                            handleSampleFieldChange(sample, 'filter', event.target.value)
                          }
                        />
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>
        </FilterSection>

        <FilterSection
          title="Locations"
          open={openSections.locations}
          onToggle={handleSectionToggle('locations')}
          active={locationFilterCount > 0}
          meta={summarizeSection(locationFilterCount)}
          controls={
            <>
              <label className="variant-summary-select-field">
                <span>Panel</span>
                <select name="panel_id" value={draftFilters.panel_id} onChange={handleDraftFieldChange}>
                  <option value="">Any gene panel</option>
                  {panels.map((panel) => (
                    <option key={panel._id} value={panel._id}>{panel.name}</option>
                  ))}
                  {/* An applied panel missing from the list (it failed to load) still shows
                      as applied, not as "Any gene panel". */}
                  {draftFilters.panel_id && !panels.some((panel) => panel._id === draftFilters.panel_id) ? (
                    <option value={draftFilters.panel_id}>{`Panel ${draftFilters.panel_id} (not in the panel list)`}</option>
                  ) : null}
                </select>
              </label>
            </>
          }
        >
          <div className="variant-filter-dropdown-content">
            <div className="analysis-filter-grid analysis-filter-grid--5">
              <input
                name="locus"
                placeholder="Gene or region"
                value={draftFilters.locus}
                onChange={handleDraftFieldChange}
              />
              <input
                name="gene"
                placeholder="Gene symbol"
                value={draftFilters.gene}
                onChange={handleDraftFieldChange}
              />
              <input
                name="chr"
                placeholder="Chromosome"
                value={draftFilters.chr}
                onChange={handleDraftFieldChange}
              />
              <input
                name="start"
                placeholder="Start ≥"
                value={draftFilters.start}
                onChange={handleDraftFieldChange}
              />
              <input
                name="end"
                placeholder="End ≤"
                value={draftFilters.end}
                onChange={handleDraftFieldChange}
              />
            </div>
            {/* A location that is neither a gene nor a region is named, not searched. */}
            {draftLocationProblem ? (
              <div className="variant-workspace-feedback variant-workspace-feedback--error" role="alert">
                {draftLocationProblem} Nothing was searched.
              </div>
            ) : null}
          </div>
        </FilterSection>

        <FilterSection
          title="Class and breakpoints"
          open={openSections.classAndBreakpoints}
          onToggle={handleSectionToggle('classAndBreakpoints')}
          active={classFilterCount > 0}
          meta={summarizeSection(classFilterCount)}
        >
          <div className="variant-filter-dropdown-content">
            <div className="variant-filter-choice-group">
              <p className="variant-filter-choice-title">SV type</p>
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {SV_TYPE_OPTIONS.map((option) => (
                  <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={parseCommaSeparatedValues(draftFilters.type).includes(option.value)}
                      onChange={() => toggleDraftFilterListValue('type', option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="analysis-filter-grid analysis-filter-grid--5">
              <input
                name="source"
                placeholder="Callset / source"
                value={draftFilters.source}
                onChange={handleDraftFieldChange}
              />
              <input
                name="minLength"
                placeholder="Min length"
                value={draftFilters.minLength}
                onChange={handleDraftFieldChange}
              />
              <input
                name="length"
                placeholder="Exact length"
                value={draftFilters.length}
                onChange={handleDraftFieldChange}
              />
              <input
                name="remote_chr"
                placeholder="Remote chr"
                value={draftFilters.remote_chr}
                onChange={handleDraftFieldChange}
              />
              <input
                name="remote_start"
                placeholder="Remote start ≥"
                value={draftFilters.remote_start}
                onChange={handleDraftFieldChange}
              />
            </div>
          </div>
        </FilterSection>

        <FilterSection
          title="Needlr annotations"
          open={openSections.needlr}
          onToggle={handleSectionToggle('needlr')}
          active={needlrFilterCount > 0}
          meta={summarizeSection(needlrFilterCount)}
        >
          <div className="variant-filter-dropdown-content">
            <div className="analysis-filter-grid analysis-filter-grid--5">
              <input
                name="phenotype"
                placeholder="OMIM or GenCC phenotype"
                value={draftFilters.phenotype}
                onChange={handleDraftFieldChange}
              />
              <input
                name="hpo"
                placeholder="HPO term"
                value={draftFilters.hpo}
                onChange={handleDraftFieldChange}
              />
              <input
                name="moi"
                placeholder="MOI"
                value={draftFilters.moi}
                onChange={handleDraftFieldChange}
              />
              <input
                name="gencc_support"
                placeholder="GenCC support"
                value={draftFilters.gencc_support}
                onChange={handleDraftFieldChange}
              />
            </div>
            <div className="variant-frequency-slider-grid">
              {[
                ['max_control_af', 'Control cohort AF'],
                ['max_population_af', 'Population AF'],
              ].map(([key, label]) => (
                <div key={key} className="variant-frequency-slider-row">
                  <div className="variant-frequency-slider-header">
                    <span>{label}</span>
                    <span>{formatPercentFilterValue(draftFilters[key as keyof StructuralFilterState])}</span>
                  </div>
                  <input
                    type="range"
                    min={0}
                    max={10}
                    step={0.1}
                    value={normalizePercentValue(
                      String(Number(draftFilters[key as keyof StructuralFilterState] || '0') * 100),
                    )}
                    onChange={(event) =>
                      setFrequencyFromPercent(
                        key as 'max_control_af' | 'max_population_af',
                        Number(event.target.value),
                      )
                    }
                  />
                  <button
                    type="button"
                    className="button-secondary variant-frequency-clear"
                    onClick={() =>
                      setDraftFilterValue(key as 'max_control_af' | 'max_population_af', '')
                    }
                  >
                    Clear
                  </button>
                </div>
              ))}
            </div>
            <div className="analysis-filter-grid analysis-filter-grid--3">
              <input
                name="min_pli"
                placeholder="Min pLI"
                value={draftFilters.min_pli}
                onChange={handleDraftFieldChange}
              />
            </div>
            <div className="variant-checkbox-grid variant-checkbox-grid--small">
              {REGION_FLAG_OPTIONS.map((flag) => {
                const selectedFlags = parseCommaSeparatedValues(draftFilters.region_flags);
                const checked = selectedFlags.includes(flag);
                return (
                  <label key={flag} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleDraftFilterListValue('region_flags', flag)}
                    />
                    {flag}
                  </label>
                );
              })}
            </div>
          </div>
        </FilterSection>

        <FilterSection
          title="Review"
          open={openSections.review}
          onToggle={handleSectionToggle('review')}
          active={reviewFilterCount > 0}
          meta={summarizeSection(reviewFilterCount)}
        >
          <div className="variant-filter-dropdown-content">
            <div className="variant-filter-choice-group">
              <p className="variant-filter-choice-title">Classification</p>
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {ACMG_CLASSIFICATION_TAGS.map((option) => (
                  <label key={option.key} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={parseCommaSeparatedValues(draftFilters.classification).includes(option.label)}
                      onChange={() => toggleDraftFilterListValue('classification', option.label)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="variant-filter-choice-group">
              <p className="variant-filter-choice-title">Tags</p>
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {reviewTagOptions.map((option) => (
                  <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={parseCommaSeparatedValues(draftFilters.review_tags).includes(option.value)}
                      onChange={() => toggleDraftFilterListValue('review_tags', option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
            <div className="variant-filter-choice-group">
              <p className="variant-filter-choice-title">Exclude tags</p>
              <div className="variant-checkbox-grid variant-checkbox-grid--small">
                {excludeReviewTagOptions.map((option) => (
                  <label key={option.value} className="analysis-checkbox variant-compact-checkbox">
                    <input
                      type="checkbox"
                      checked={parseCommaSeparatedValues(draftFilters.exclude_review_tags).includes(option.value)}
                      onChange={() => toggleDraftFilterListValue('exclude_review_tags', option.value)}
                    />
                    {option.label}
                  </label>
                ))}
              </div>
            </div>
            <label className="analysis-checkbox variant-compact-checkbox">
              <input
                type="checkbox"
                checked={draftFilters.has_notes === 'true'}
                onChange={(event) =>
                  setDraftFilterValue('has_notes', event.target.checked ? 'true' : '')
                }
              />
              Has notes
            </label>
          </div>
        </FilterSection>
      </FilterAccordion>
    </form>
  );
};

export default StructuralVariantFilterForm;
