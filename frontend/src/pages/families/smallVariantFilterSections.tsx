// The small-variant filter form's sections (#528): the shell each one shares, and one
// component per section. Each reads what it needs from the form through
// FilterSectionForm and computes its own summary count and quick filters.
import type { ChangeEvent, ReactNode, SyntheticEvent } from 'react';
import { joinFilterValues, parseCommaSeparatedValues } from '../../lib/sampleFilterState';
import {
  COLLABORATION_QUICK_TAGS,
  REVIEW_CLASSIFICATION_OPTIONS,
  resolveCarrierScreeningCoupleMembers,
  type FamilyMember,
  type GenePanel,
  type SmallFilterState,
  type SmallVariantSearchState,
} from './smallVariantSearch';
import { GENOTYPE_GROUP_HINTS } from '../../lib/genotypes';

const TYPE_OPTIONS = ['', 'SNV', 'INDEL', 'MNV'];
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

export const GT_GROUP_OPTIONS = [
  { id: 'hom-group', values: ['1/1', '1|1'] },
  { id: 'het-group', values: ['0/1', '1/0', '0|1', '1|0'] },
  { id: 'ref-group', values: ['0/0', '0|0', './.', 'absent'] },
] as const;

export type GtGroupId = (typeof GT_GROUP_OPTIONS)[number]['id'];

// Keeps a click on a quick control in a section's summary bar from also toggling the
// section open or closed.
const stopSummaryInteraction = (event: SyntheticEvent<HTMLElement>) => {
  event.stopPropagation();
};

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

export const DEFAULT_OPEN_SECTIONS = {
  phenotype: false,
  svSecondHit: false,
  inheritance: false,
  categories: false,
  pathogenicity: false,
  annotations: false,
  inSilico: false,
  frequency: false,
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
  numericFilterEquals: (value: string, target: string) => boolean;
  setSampleQualityThresholds: (thresholds: {
    qual: string;
    dp: string;
    af: string;
    ad_alt: string;
  }) => void;
  updateInheritanceGenotypes: (strategy: (member: FamilyMember) => ReadonlySet<GtGroupId>) => void;
  activeSampleMemberCount: number;
  carrierScreeningCouple: ReturnType<typeof resolveCarrierScreeningCoupleMembers>;
  clinvarOptions: FilterOption[];
  standardTagOptions: FilterOption[];
  customTagOptions: FilterOption[];
}

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
summarizeSection(
            draftFilters.prioritize === 'true' ? 1 : 0,
            'Prioritization off',
          )
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
summarizeSection(
            draftFilters.require_sv_second_hit === 'true' ? 1 : 0,
            'Off',
          )
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
    activeSampleMemberCount,
    carrierScreeningCouple,
    draftFilters,
    handleDraftFieldChange,
    handleGtToggle,
    handleSampleFieldChange,
    handleSectionToggle,
    members,
    openSections,
    sampleDraftFilters,
    setDraftFilterValue,
    setSampleQualityThresholds,
    summarizeSection,
    toggleSingleValueCheckbox,
    updateInheritanceGenotypes,
  } = form;
  const inheritanceFilterCount =
    activeSampleMemberCount +
    (draftFilters.expanded_carrier_screening === 'true' ? 1 : 0) +
    countNonEmpty(draftFilters.inheritance, draftFilters.type, draftFilters.source, draftFilters.ps);

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
    </FilterSection>
  );
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
summarizeSection(
            parseCommaSeparatedValues(draftFilters.category).length,
            'All categories',
          )
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
  const pathogenicityFilterCount = parseCommaSeparatedValues(draftFilters.clinvar).length;
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
    toggleDraftFilterListValue,
  } = form;
  const annotationFilterCount =
    parseCommaSeparatedValues(draftFilters.impact).length +
    parseCommaSeparatedValues(draftFilters.effect).length +
    countNonEmpty(
      draftFilters.transcript,
      draftFilters.rsid,
      draftFilters.hgvsc,
      draftFilters.hgvsp,
      draftFilters.canonical_only,
      draftFilters.mane_only,
      draftFilters.lof_only,
    );
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

  const handleImpactCategoryToggle = (impact: string, checked: boolean) => {
    const consequences = CONSEQUENCE_BY_IMPACT[impact] ?? [];
    const impactSet = new Set(parseCommaSeparatedValues(draftFilters.impact));
    const effectSet = new Set(parseCommaSeparatedValues(draftFilters.effect));

    if (checked) {
      impactSet.add(impact);
      consequences.forEach((value) => effectSet.add(value));
    } else {
      impactSet.delete(impact);
      consequences.forEach((value) => effectSet.delete(value));
    }

    setDraftFilterValue('impact', joinFilterValues(impactSet));
    setDraftFilterValue('effect', joinFilterValues(effectSet));
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
              <option value="high_impact">High</option>
              <option value="moderate_to_high">Mod+High</option>
              <option value="all_coding">Coding</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
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
  const inSilicoFilterCount = countNonEmpty(
    draftFilters.min_cadd,
    draftFilters.min_revel,
    draftFilters.min_spliceai,
    draftFilters.sift,
    draftFilters.polyphen,
  );
  return (
    <FilterSection
      title="In Silico"
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
    </FilterSection>
  );
};

export const FrequencyFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    numericFilterEquals,
    openSections,
    setDraftFilterValue,
    summarizeSection,
  } = form;
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
      // Popmax has to be constrained too. An annotation run that emits VEP's MAX_AF but
      // no gnomAD exome/genome AF leaves those two filters comparing against a missing
      // value, which the query treats as 0 — so a variant at popmax 1.0 passed a
      // "gnomAD <1%" preset untouched.
      setDraftFilterValue('max_gnomad_popmax_af', FREQUENCY_QUICK_GNOMAD_AF);
      setDraftFilterValue('max_topmed_af', '');
      setDraftFilterValue('max_gnomad_ac', '');
      setDraftFilterValue('max_gnomad_hom_count', FREQUENCY_QUICK_GNOMAD_COUNT);
      setDraftFilterValue('max_gnomad_hemi_count', FREQUENCY_QUICK_GNOMAD_COUNT);
    }
  };

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
      !draftFilters.max_topmed_af.trim() &&
      !draftFilters.max_gnomad_ac.trim() &&
      numericFilterEquals(draftFilters.max_gnomad_exomes_af, FREQUENCY_QUICK_GNOMAD_AF) &&
      numericFilterEquals(draftFilters.max_gnomad_genomes_af, FREQUENCY_QUICK_GNOMAD_AF) &&
      numericFilterEquals(draftFilters.max_gnomad_popmax_af, FREQUENCY_QUICK_GNOMAD_AF) &&
      numericFilterEquals(draftFilters.max_gnomad_hom_count, FREQUENCY_QUICK_GNOMAD_COUNT) &&
      numericFilterEquals(draftFilters.max_gnomad_hemi_count, FREQUENCY_QUICK_GNOMAD_COUNT)
    ) {
      return 'gnomad_rare';
    }
    return 'custom';
  })();
  return (
    <FilterSection
      title="Frequency"
      open={openSections.frequency}
      onToggle={handleSectionToggle('frequency')}
      meta={summarizeSection(frequencyFilterCount, 'No filters')}
      controls={
        <>
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
        </>
      }
    >
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

export const LocationsFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    offers,
    openSections,
    panels,
    summarizeSection,
  } = form;
  const locationFilterCount =
    countNonEmpty(draftFilters.panel_id) +
    (draftFilters.gene.trim() ? countTextAreaEntries(draftFilters.gene) : 0) +
    (draftFilters.intervals.trim() ? countTextAreaEntries(draftFilters.intervals) : 0);
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
      </div>
    </FilterSection>
  );
};

export const ExcludeFilterSection = ({ form }: { form: FilterSectionForm }) => {
  const {
    clinvarOptions,
    customTagOptions,
    draftFilters,
    handleDraftFieldChange,
    handleSectionToggle,
    offers,
    openSections,
    setDraftFilterValue,
    standardTagOptions,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  const excludeFilterCount =
    parseCommaSeparatedValues(draftFilters.exclude_clinvar).length +
    parseCommaSeparatedValues(draftFilters.exclude_review_tags).length +
    (draftFilters.exclude_gene.trim() ? countTextAreaEntries(draftFilters.exclude_gene) : 0) +
    (draftFilters.exclude_intervals.trim()
      ? countTextAreaEntries(draftFilters.exclude_intervals)
      : 0);
  const selectedExcludeClinvarValues = parseCommaSeparatedValues(draftFilters.exclude_clinvar);
  const selectedExcludeReviewTagValues = parseCommaSeparatedValues(draftFilters.exclude_review_tags);
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
    setDraftFilterValue,
    standardTagOptions,
    summarizeSection,
    toggleDraftFilterListValue,
  } = form;
  const reviewFilterCount =
    parseCommaSeparatedValues(draftFilters.classification).length +
    parseCommaSeparatedValues(draftFilters.review_tags).length +
    (draftFilters.has_notes === 'true' ? 1 : 0);

  const selectedClassificationValues = parseCommaSeparatedValues(draftFilters.classification);
  const selectedReviewTagValues = parseCommaSeparatedValues(draftFilters.review_tags);
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
              <option value="pathogenic_vus">P/LP/VUS</option>
              <option value="review_tag">Review tag</option>
              <option value="custom">Custom</option>
            </select>
          </label>
        </>
      }
    >
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
