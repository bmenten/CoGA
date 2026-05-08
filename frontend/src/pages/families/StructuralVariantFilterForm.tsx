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
  ACMG_CLASSIFICATION_TAGS,
  countPresetRules,
  getPresetScopeLabel,
  sortTagDefinitions,
} from './smallVariantSearch';

type StructuralVariantFilterFormProps = Pick<
  StructuralVariantSearchState,
  | 'activeFilterChips'
  | 'applyPreset'
  | 'draftFilters'
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
  feedback?: { type: 'success' | 'error'; message: string } | null;
  onSaveCurrentPreset: (payload: { name: string; description?: string; scope: 'family' | 'global' }) => void | Promise<void>;
  panels: StructuralGenePanel[];
  presets: StructuralVariantFilterPreset[];
  savingPreset?: boolean;
  tags: StructuralVariantTagDefinition[];
};

const PRESETS: { value: StructuralPreset; label: string }[] = [
  { value: 'dominant', label: 'Dominant' },
  { value: 'recessive', label: 'Recessive-like' },
  { value: 'any_affected', label: 'Any affected' },
];

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

const toggleCommaValue = (value: string, item: string) => {
  const selected = new Set(
    value
      .split(',')
      .map((entry) => entry.trim())
      .filter(Boolean),
  );
  if (selected.has(item)) {
    selected.delete(item);
  } else {
    selected.add(item);
  }
  return Array.from(selected).join(', ');
};

const countNonEmpty = (...values: string[]) => values.filter((value) => value.trim()).length;

const splitSelectedValues = (value: string) =>
  value
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);

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

const StructuralVariantFilterForm = ({
  activeFilterChips,
  applyPreset,
  draftFilters,
  handleGtToggle,
  handleReset,
  handleSampleFieldChange,
  handleSearch,
  orderedMembers,
  applySavedPreset,
  feedback,
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
  const [openSections, setOpenSections] = useState({
    support: true,
    locations: false,
    classAndBreakpoints: false,
    needlr: false,
    review: false,
  });
  const [saveOpen, setSaveOpen] = useState(false);
  const [selectedPreset, setSelectedPreset] = useState('');
  const [presetName, setPresetName] = useState('');
  const [presetDescription, setPresetDescription] = useState('');

  const handleDraftFieldChange = (event: ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
    setDraftFilterValue(event.target.name as keyof StructuralFilterState, event.target.value);
  };

  const handleSectionToggle =
    (section: keyof typeof openSections) => (event: SyntheticEvent<HTMLDetailsElement>) => {
      const nextOpen = event.currentTarget.open;
      setOpenSections((prev) => ({ ...prev, [section]: nextOpen }));
    };

  const stopSummaryInteraction = (event: SyntheticEvent<HTMLElement>) => {
    event.stopPropagation();
  };

  const getActiveChipLabel = (chip: ActiveStructuralFilterChip) => {
    if (chip.kind === 'top' && chip.key === 'panel_id') {
      const panel = panels.find((entry) => entry._id === draftFilters.panel_id);
      return `Gene panel: ${panel?.name || draftFilters.panel_id}`;
    }
    return chip.label;
  };

  const sampleFilterCount = orderedMembers.reduce((count, member) => {
    const filter = sampleDraftFilters[member.sample_id];
    if (!filter) return count;
    const genotypeActive = filter.gt.length > 0 && filter.gt.length < 12;
    const thresholdActive = Boolean(filter.qual || filter.read_support || filter.filter);
    return count + (genotypeActive || thresholdActive ? 1 : 0);
  }, 0);
  const supportFilterCount = sampleFilterCount + countNonEmpty(draftFilters.type, draftFilters.source);
  const locationFilterCount = countNonEmpty(
    draftFilters.locus,
    draftFilters.panel_id,
    draftFilters.gene,
    draftFilters.chr,
    draftFilters.start,
    draftFilters.end,
  );
  const classFilterCount = countNonEmpty(
    draftFilters.minLength,
    draftFilters.length,
    draftFilters.remote_chr,
    draftFilters.remote_start,
  );
  const needlrFilterCount =
    countNonEmpty(
      draftFilters.inheritance,
      draftFilters.phenotype,
      draftFilters.hpo,
      draftFilters.moi,
      draftFilters.gencc_support,
      draftFilters.max_control_af,
      draftFilters.max_population_af,
      draftFilters.min_pli,
    ) + splitSelectedValues(draftFilters.region_flags).length;
  const reviewFilterCount =
    splitSelectedValues(draftFilters.classification).length +
    splitSelectedValues(draftFilters.review_tags).length +
    splitSelectedValues(draftFilters.exclude_review_tags).length +
    (draftFilters.has_notes === 'true' ? 1 : 0);
  const sortedTags = sortTagDefinitions(tags);
  const nonClassificationTags = sortedTags.filter(
    (tag) => !ACMG_CLASSIFICATION_TAGS.some((option) => option.key === tag.key),
  );

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
            <select
              className="variant-saved-filter-select"
              value={selectedPreset}
              onChange={(event) => setSelectedPreset(event.target.value)}
            >
              <option value="">Saved filters</option>
              {presets.map((preset) => (
                <option key={preset._id} value={preset._id}>
                  {preset.name} ({getPresetScopeLabel(preset.scope)}, {countPresetRules(preset)})
                </option>
              ))}
            </select>
            <button
              type="button"
              className="button-secondary"
              disabled={!selectedPreset}
              onClick={() => {
                const preset = presets.find((entry) => entry._id === selectedPreset);
                if (preset) applySavedPreset(preset);
              }}
            >
              Apply saved
            </button>
            {PRESETS.map((preset) => (
              <button
                key={preset.value}
                type="button"
                className="analysis-pill analysis-pill-button"
                onClick={() => applyPreset(preset.value)}
              >
                {preset.label}
              </button>
            ))}
            <button type="button" className="button-secondary" onClick={handleReset}>
              Clear all filters
            </button>
            <button type="button" className="button-secondary" onClick={() => setSaveOpen((value) => !value)}>
              Save current
            </button>
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
                onClick={() => {
                  onSaveCurrentPreset({
                    name: presetName.trim(),
                    description: presetDescription.trim() || undefined,
                    scope: 'family',
                  });
                  setPresetName('');
                  setPresetDescription('');
                  setSaveOpen(false);
                }}
              >
                {savingPreset ? 'Saving...' : 'Save'}
              </button>
            </div>
            <input
              placeholder="Description"
              value={presetDescription}
              onChange={(event) => setPresetDescription(event.target.value)}
            />
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
                className="variant-filter-chip"
                onClick={() => removeActiveFilterChip(chip)}
                title="Remove filter"
              >
                {getActiveChipLabel(chip)}
                <span aria-hidden="true">×</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}

      <section className="variant-search-section">
        <div className="variant-filter-dropdown-grid">
          <details
            className="variant-filter-dropdown"
            open={openSections.support}
            onToggle={handleSectionToggle('support')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Inheritance and Support</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(supportFilterCount)}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>SV type</span>
                  <input
                    name="type"
                    placeholder="Any"
                    value={draftFilters.type}
                    onChange={handleDraftFieldChange}
                  />
                </label>
                <label className="variant-summary-select-field">
                  <span>Source</span>
                  <input
                    name="source"
                    placeholder="Any"
                    value={draftFilters.source}
                    onChange={handleDraftFieldChange}
                  />
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            </summary>
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
                            { value: 'hom-group', label: 'Hom', group: ['1/1', '1|1'] },
                            { value: 'het-group', label: 'Het', group: ['0/1', '1/0', '0|1', '1|0'] },
                            { value: 'ref-group', label: 'WT', group: ['0/0', '0|0', './.', 'absent'] },
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
                  {summarizeSection(locationFilterCount)}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
                <label className="variant-summary-select-field">
                  <span>Panel</span>
                  <select name="panel_id" value={draftFilters.panel_id} onChange={handleDraftFieldChange}>
                    <option value="">Any gene panel</option>
                    {panels.map((panel) => (
                      <option key={panel._id} value={panel._id}>{panel.name}</option>
                    ))}
                  </select>
                </label>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            </summary>
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
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.classAndBreakpoints}
            onToggle={handleSectionToggle('classAndBreakpoints')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Class and Breakpoints</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(classFilterCount)}
                </span>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <div className="analysis-filter-grid analysis-filter-grid--5">
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
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.needlr}
            onToggle={handleSectionToggle('needlr')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Needlr Annotations</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(needlrFilterCount)}
                </span>
              </span>
              <span
                className="variant-filter-dropdown-summary-controls"
                onMouseDown={stopSummaryInteraction}
                onClick={stopSummaryInteraction}
              >
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
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            </summary>
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
                  const selectedFlags = splitSelectedValues(draftFilters.region_flags);
                  const checked = selectedFlags.includes(flag);
                  return (
                    <label key={flag} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setDraftFilterValue(
                            'region_flags',
                            toggleCommaValue(draftFilters.region_flags, flag),
                          )
                        }
                      />
                      {flag}
                    </label>
                  );
                })}
              </div>
            </div>
          </details>

          <details
            className="variant-filter-dropdown"
            open={openSections.review}
            onToggle={handleSectionToggle('review')}
          >
            <summary className="variant-filter-dropdown-summary">
              <span className="variant-filter-dropdown-summary-copy">
                <span className="variant-filter-dropdown-title">Review</span>
                <span className="variant-filter-dropdown-meta">
                  {summarizeSection(reviewFilterCount)}
                </span>
              </span>
              <span className="variant-filter-dropdown-caret" aria-hidden="true">▾</span>
            </summary>
            <div className="variant-filter-dropdown-content">
              <div className="variant-filter-choice-group">
                <p className="variant-filter-choice-title">Classification</p>
                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                  {ACMG_CLASSIFICATION_TAGS.map((option) => (
                    <label key={option.key} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={splitSelectedValues(draftFilters.classification).includes(option.label)}
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
                  {nonClassificationTags.map((tag) => (
                    <label key={tag.key} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={splitSelectedValues(draftFilters.review_tags).includes(tag.key)}
                        onChange={() => toggleDraftFilterListValue('review_tags', tag.key)}
                      />
                      {tag.label}
                    </label>
                  ))}
                </div>
              </div>
              <div className="variant-filter-choice-group">
                <p className="variant-filter-choice-title">Exclude tags</p>
                <div className="variant-checkbox-grid variant-checkbox-grid--small">
                  {nonClassificationTags.map((tag) => (
                    <label key={tag.key} className="analysis-checkbox variant-compact-checkbox">
                      <input
                        type="checkbox"
                        checked={splitSelectedValues(draftFilters.exclude_review_tags).includes(tag.key)}
                        onChange={() => toggleDraftFilterListValue('exclude_review_tags', tag.key)}
                      />
                      {tag.label}
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

export default StructuralVariantFilterForm;
