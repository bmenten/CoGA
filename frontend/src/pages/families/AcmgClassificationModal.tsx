import { useEffect, useMemo, useRef, useState } from 'react';
import { useModalDialog } from '../../lib/useModalDialog';
import { useQuery } from '@tanstack/react-query';

import api from '../../lib/api';
import {
  ACMG_CRITERIA_BY_CODE,
  BENIGN_CRITERIA,
  PATHOGENIC_CRITERIA,
  STRENGTH_LABELS,
  buildInitialSelections,
  computeClassification,
  evaluateAcmg,
  evaluateMitoAcmg,
  parentLinksFromRelationships,
  type AcmgCriterionCode,
  type AcmgCriterionDef,
  type AcmgGeneContext,
  type AcmgSelection,
  type AcmgStrength,
} from '../../lib/acmg';
import AcmgScaleBar from './AcmgScaleBar';
import {
  buildLiteraturePubmedHref,
  buildSmallVariantExternalLinks,
  formatLocus,
  getReviewTagStyle,
} from './smallVariantResultUtils';
import {
  ACMG_AUTO_MANAGED_TAG_KEYS,
  ACMG_VUS_TIER_TAG_BY_TIER,
  getTagDefinitionMap,
  normalizeTagKeys,
  sortTagDefinitions,
  tagDefinitionLabel,
  type AcmgReviewPayload,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantReviewSavePayload,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import type { AcmgFamilyContext } from '../../lib/acmg';
import { apiPath } from '../../lib/apiPath';
import type { ApiFamilyRelationship } from '../../lib/apiTypes';
import QueryFailure from '../../components/QueryFailure';

type AcmgClassificationModalProps = {
  familyId?: string;
  projectId?: string;
  variant: SmallVariant;
  members?: FamilyMember[];
  // The family's pedigree links: PS2/PM6 compare the proband with the parents linked here.
  relationships?: ApiFamilyRelationship[];
  tagDefinitions?: SmallVariantTagDefinition[];
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion?: string;
  onClose: () => void;
  onSave: (payload: SmallVariantReviewSavePayload) => Promise<void>;
  isPending?: boolean;
  errorMessage?: string | null;
};

type HpoAnnotationLite = {
  sample_id: string;
  hpo_id: string;
  label: string;
  status: 'present' | 'absent' | 'unknown';
};

// Stable empty defaults — fresh `[]` literals as render-time defaults would change
// identity every render and retrigger the seeding effect in an infinite loop.
const EMPTY_MEMBERS: FamilyMember[] = [];
const EMPTY_RELATIONSHIPS: ApiFamilyRelationship[] = [];
const EMPTY_HPO: HpoAnnotationLite[] = [];
const EMPTY_TAGS: SmallVariantTagDefinition[] = [];

// Slice of GET /genes/profile we read for gene-level ACMG context.
type GeneProfileResponse = {
  extra?: {
    clingen_dosage_assertions?: Array<{ haploinsufficiency?: string | null }>;
    gencc_assertions?: Array<{ moi_title?: string | null }>;
    hpo_terms?: Array<{ hpo_id?: string | null }>;
  };
};

function toGeneContext(profile: GeneProfileResponse | undefined): AcmgGeneContext | undefined {
  if (!profile?.extra) return undefined;
  const extra = profile.extra;
  const haplo = (extra.clingen_dosage_assertions ?? [])
    .map((a) => a.haploinsufficiency)
    .find((value): value is string => Boolean(value));
  return {
    clingenDosageHaploinsufficiency: haplo ?? null,
    modesOfInheritance: (extra.gencc_assertions ?? [])
      .map((a) => a.moi_title)
      .filter((value): value is string => Boolean(value)),
    geneHpoIds: (extra.hpo_terms ?? [])
      .map((t) => t.hpo_id)
      .filter((value): value is string => Boolean(value)),
  };
}

function savedSelections(payload: AcmgReviewPayload | null | undefined): AcmgSelection[] {
  if (!payload?.criteria) return [];
  return payload.criteria
    .filter((c) => ACMG_CRITERIA_BY_CODE[c.code as AcmgCriterionCode])
    .map((c) => ({
      code: c.code as AcmgCriterionCode,
      accepted: c.accepted,
      strength: c.strength as AcmgStrength,
      evidence: c.evidence ?? undefined,
      autoSuggested: c.auto_suggested,
    }));
}

export default function AcmgClassificationModal({
  familyId,
  projectId,
  variant,
  members = EMPTY_MEMBERS,
  relationships = EMPTY_RELATIONSHIPS,
  tagDefinitions = EMPTY_TAGS,
  speciesName,
  assemblyName,
  assemblyVersion,
  onClose,
  onSave,
  isPending = false,
  errorMessage = null,
}: AcmgClassificationModalProps) {
  // Working selection state keyed by criterion code. A criterion absent from the
  // map renders as an unaccepted default; toggling/strength-editing upserts it.
  const [selections, setSelections] = useState<Record<string, AcmgSelection>>({});
  const [note, setNote] = useState('');
  // Manually-managed review tags (collaboration / custom / secondary finding).
  // The computed acmg_class_* tag is derived from the criteria and excluded here.
  const [reviewTags, setReviewTags] = useState<string[]>([]);

  // #337: seed the analyst's editable state (selections/note/tags) once per variant and
  // keep refining it while the async gene-profile / HPO context resolves — but STOP the
  // moment the analyst edits anything, so a late-arriving query never wipes their
  // in-progress work.
  const seededVariantKey = useRef<string | null>(null);
  const analystEdited = useRef(false);
  const markEdited = () => {
    analystEdited.current = true;
  };

  const tagMap = useMemo(() => getTagDefinitionMap(tagDefinitions), [tagDefinitions]);
  // Tags the analyst can toggle by hand — everything except the auto-managed
  // ACMG class tags, which the selected criteria own. A deleted tag is listed only for a
  // review that holds it, marked, so it can be removed; it is added to no review.
  const heldTags = variant.review?.tags;
  const editableTagDefinitions = useMemo(() => {
    const autoKeys = ACMG_AUTO_MANAGED_TAG_KEYS as readonly string[];
    const held = new Set(heldTags ?? []);
    return sortTagDefinitions(tagDefinitions).filter(
      (def) => !autoKeys.includes(def.key) && (def.is_active !== false || held.has(def.key)),
    );
  }, [tagDefinitions, heldTags]);
  const toggleReviewTag = (tagKey: string) => {
    markEdited();
    setReviewTags((current) =>
      current.includes(tagKey)
        ? current.filter((key) => key !== tagKey)
        : [...current, tagKey],
    );
  };

  const geneSymbol = variant.gene?.trim();
  const {
    data: geneProfile,
    isError: geneProfileFailed,
    error: geneProfileError,
    refetch: refetchGeneProfile,
  } = useQuery<GeneProfileResponse>({
    queryKey: ['acmg-gene-profile', geneSymbol, familyId, projectId],
    enabled: Boolean(geneSymbol),
    queryFn: async () => {
      const response = await api.get('/genes/profile', {
        params: { symbol: geneSymbol, family_id: familyId, project_id: projectId },
      });
      return response.data as GeneProfileResponse;
    },
  });

  // A failed profile is unknown, not a gene without a LOF mechanism or HPO terms: the
  // criteria that read it say "not assessed" (#609).
  const geneContext = useMemo<AcmgGeneContext | undefined>(
    () => (geneProfileFailed ? { unavailable: true } : toGeneContext(geneProfile)),
    [geneProfile, geneProfileFailed],
  );

  // Patient HPO terms (proband's "present" annotations) for PP4 and the PubMed link.
  const {
    data: hpoData,
    isError: hpoFailed,
    error: hpoError,
    refetch: refetchHpo,
  } = useQuery<HpoAnnotationLite[]>({
    queryKey: ['acmg-family-hpo', familyId],
    enabled: Boolean(familyId),
    queryFn: async () => {
      const response = await api.get(apiPath`/families/${familyId}/hpo`);
      return response.data as HpoAnnotationLite[];
    },
  });

  const probandSampleId = useMemo(() => {
    const proband =
      members.find((m) => (m.role ?? '').toLowerCase() === 'proband') ??
      members.find((m) => m.affected);
    return proband?.sample_id;
  }, [members]);

  const probandHpo = useMemo(() => {
    const present = (hpoData ?? EMPTY_HPO).filter(
      (a) => a.status === 'present' && (!probandSampleId || a.sample_id === probandSampleId),
    );
    return {
      ids: Array.from(new Set(present.map((a) => a.hpo_id))),
      labels: Array.from(new Set(present.map((a) => a.label).filter(Boolean))),
      unavailable: hpoFailed,
    };
  }, [hpoData, probandSampleId, hpoFailed]);

  // Trio / segregation context: each member's genotype call for this variant, and who is
  // whose parent (the member roles cannot say: a grandparent is a 'father' or 'mother' too).
  const parentLinks = useMemo(() => parentLinksFromRelationships(relationships), [relationships]);
  const familyContext = useMemo<AcmgFamilyContext>(
    () => ({
      members: members.map((member) => {
        const call = variant.genotypes.find((g) => g.sample === member.sample_id);
        return {
          sampleId: member.sample_id,
          role: member.role,
          affected: Boolean(member.affected) || member.clinical_status === 'affected',
          gt: call?.gt,
          dp: call?.dp,
          sex: member.sex,
        };
      }),
      parentLinks,
    }),
    [members, variant, parentLinks],
  );

  // External resource links (reuse the variant-card builder) + a smart PubMed search.
  const resourceLinks = useMemo(() => {
    const base = buildSmallVariantExternalLinks({ variant, speciesName, assemblyName, assemblyVersion });
    const wanted = ['gnomAD', 'ClinVar', 'DECIPHER'];
    const links = wanted
      .map((label) => base.find((entry) => entry.label === label))
      .filter((entry): entry is { label: string; href: string } => Boolean(entry));
    const pubmed = buildLiteraturePubmedHref({
      gene: variant.gene || variant.gene_id,
      proteinChange: variant.hgvsp || variant.hgvsc,
      hpoLabels: probandHpo.labels,
    });
    if (pubmed) {
      links.push({
        label: probandHpo.labels.length ? 'PubMed (gene + HPO)' : 'PubMed (gene)',
        href: pubmed,
      });
    }
    return links;
  }, [variant, speciesName, assemblyName, assemblyVersion, probandHpo.labels]);

  // (Re)seed selections when the variant changes or gene/HPO context arrives — but only
  // until the analyst edits this variant (see the refs above), so late-resolving
  // gene/HPO queries refine the suggestions without discarding in-progress work (#337).
  useEffect(() => {
    if (variant._id !== seededVariantKey.current) {
      // A different variant is being classified: reset the edit guard and (re)seed.
      seededVariantKey.current = variant._id;
      analystEdited.current = false;
    } else if (analystEdited.current) {
      return;
    }
    // PP4: pass the Monarch phenotype-specificity score when prioritization
    // populated it on the variant (otherwise PP4 falls back to HPO overlap).
    const phenotypeContext = {
      probandHpoIds: probandHpo.ids,
      probandHpoUnavailable: probandHpo.unavailable,
      phenotypeScore: variant.priority?.phenotype_score ?? null,
      phenotypeMatches: variant.priority?.phenotype_matches,
    };
    // Mitochondrial variants use the mt-specific evaluator (McCormick 2020).
    const suggestions =
      variant.chr === 'MT' && variant.mito
        ? evaluateMitoAcmg(variant, variant.mito, geneContext, phenotypeContext)
        : evaluateAcmg(variant, geneContext, phenotypeContext, familyContext);
    const initial = buildInitialSelections(suggestions, savedSelections(variant.review?.acmg));
    setSelections(Object.fromEntries(initial.map((s) => [s.code, s])));
    setNote(variant.review?.note ?? '');
    const autoKeys = ACMG_AUTO_MANAGED_TAG_KEYS as readonly string[];
    setReviewTags((variant.review?.tags ?? []).filter((tag) => !autoKeys.includes(tag)));
  }, [variant, geneContext, familyContext, probandHpo]);

  const selectionList = useMemo(() => Object.values(selections), [selections]);
  const classification = useMemo(() => computeClassification(selectionList), [selectionList]);

  // Custom hover tooltip (native title only yields a help cursor here).
  const [tip, setTip] = useState<TipState | null>(null);
  const showTip = (def: AcmgCriterionDef, selection: AcmgSelection | undefined, el: HTMLElement) => {
    const rect = el.getBoundingClientRect();
    const below = rect.top < window.innerHeight / 2;
    setTip({
      def,
      selection,
      left: Math.min(rect.left, window.innerWidth - 340),
      top: below ? rect.bottom + 6 : rect.top - 6,
      below,
    });
  };
  const hideTip = () => setTip(null);

  const upsert = (code: AcmgCriterionCode, patch: Partial<AcmgSelection>) => {
    markEdited();
    setSelections((current) => {
      const def = ACMG_CRITERIA_BY_CODE[code];
      const existing =
        current[code] ??
        ({
          code,
          accepted: false,
          strength: def.defaultStrength,
          autoSuggested: false,
        } satisfies AcmgSelection);
      return { ...current, [code]: { ...existing, ...patch } };
    });
  };

  const handleSave = async () => {
    const accepted = selectionList.filter((s) => s.accepted);
    const criteria = selectionList
      .filter((s) => s.accepted || s.autoSuggested)
      .map((s) => ({
        code: s.code,
        strength: s.strength,
        accepted: s.accepted,
        evidence: s.evidence ?? null,
        auto_suggested: s.autoSuggested,
      }));

    const acmg: AcmgReviewPayload = {
      criteria,
      point_total: classification.points,
      classification: classification.label,
      vus_tier: classification.vusTier,
    };

    // Reflect the computed class in the review tags so variant cards and summaries
    // pick it up — combine the analyst's manually-toggled tags with the computed
    // acmg_class_* tag and, for a VUS, the matching acmg_vus_* tier tag (the manual
    // set already excludes any auto-managed ACMG tag).
    const autoTags = classification.vusTier
      ? [classification.classKey, ACMG_VUS_TIER_TAG_BY_TIER[classification.vusTier]]
      : [classification.classKey];
    const tags = accepted.length
      ? normalizeTagKeys([...reviewTags, ...autoTags])
      : normalizeTagKeys(reviewTags);

    const payload: SmallVariantReviewSavePayload = {
      classification: accepted.length ? classification.label : undefined,
      tags,
      note: note.trim() || undefined,
      acmg,
    };

    try {
      await onSave(payload);
    } catch {
      // Parent keeps the dialog open and surfaces the error.
    }
  };

  // Escape, focus trap, and a check before unsaved input is discarded (#529).
  const dialog = useModalDialog({
    onClose,
    discardMessage: 'Discard your unsaved changes to this ACMG classification?',
  });

  return (
    <div className="modal-backdrop" role="presentation" {...dialog.backdropProps}>
      <div
        className="modal-surface surface-card variant-review-modal acmg-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="acmg-classification-title"
        ref={dialog.dialogRef}
        tabIndex={-1}
        {...dialog.surfaceProps}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">ACMG classification</p>
            <h2 id="acmg-classification-title" className="catalog-card-title">
              {variant.gene || variant.gene_id || 'Intergenic variant'}
            </h2>
            <p className="variant-review-modal-subtitle">
              {formatLocus(variant)} · {variant.hgvsp || variant.hgvsc || variant.effect || variant.type}
            </p>
            {resourceLinks.length ? (
              <div className="acmg-resource-links">
                {resourceLinks.map((link) => (
                  <a
                    key={link.label}
                    href={link.href}
                    target="_blank"
                    rel="noreferrer"
                    className="acmg-resource-link"
                  >
                    {link.label}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
          <button type="button" className="button-secondary" onClick={dialog.requestClose}>
            Close
          </button>
        </div>

        <div className="variant-review-modal-body acmg-modal-body">
          {/* A lookup that failed changes the suggestions: say which, and why (#609). */}
          {geneProfileFailed ? (
            <QueryFailure
              what={`the gene profile of ${geneSymbol}`}
              error={geneProfileError}
              onRetry={() => void refetchGeneProfile()}
              consequence="PVS1, BS2 and PP4 are not assessed from it: review them by hand."
            />
          ) : null}
          {hpoFailed ? (
            <QueryFailure
              what="the family's HPO terms"
              error={hpoError}
              onRetry={() => void refetchHpo()}
              consequence="PP4 is not assessed from them: review the phenotype match by hand."
            />
          ) : null}
          {errorMessage ? (
            <div className="variant-workspace-feedback variant-workspace-feedback--error">
              {errorMessage}
            </div>
          ) : null}
          {variant.review?.acmg_unreadable ? (
            <div
              className="variant-workspace-feedback variant-workspace-feedback--warning"
              role="alert"
            >
              The ACMG classification stored for this variant could not be read, so its criteria are not
              shown below. Saving replaces it with what you select here.
            </div>
          ) : null}

          <AcmgScaleBar classification={classification} />

          <p className="acmg-modal-disclaimer">
            Auto-evaluated from the variant, trio and gene data:
            <span className="acmg-key acmg-key--applied"> checked</span> = data supports it,
            <span className="acmg-key acmg-key--consider"> ●</span> = consider,
            <span className="acmg-key acmg-key--against"> ✕</span> = data argues against,
            <span className="acmg-key acmg-key--na"> n/a</span> = unlikely for this variant type.
            All of them can be overridden; hover a criterion for its evidence.
          </p>

          <div className="acmg-family-grid">
            {FAMILY_GROUPS.map((group) => (
              <FamilyGroup
                key={group.family}
                group={group}
                selections={selections}
                onToggle={upsert}
                onShowTip={showTip}
                onHideTip={hideTip}
              />
            ))}
          </div>

          {editableTagDefinitions.length ? (
            <div className="acmg-modal-tags">
              <p className="acmg-modal-tags-label">Review tags</p>
              <p className="acmg-modal-tags-hint">
                Adjust the variant&rsquo;s review tags — selected tags appear filled. The ACMG class
                tag is set automatically from the criteria above.
              </p>
              <div className="acmg-modal-tags-list">
                {editableTagDefinitions.map((def) => {
                  const active = reviewTags.includes(def.key);
                  return (
                    <button
                      key={def.key}
                      type="button"
                      className={`acmg-tag-toggle${active ? ' acmg-tag-toggle--active' : ''}`}
                      style={active ? getReviewTagStyle(def.key, tagMap) : undefined}
                      aria-pressed={active}
                      title={def.description ?? undefined}
                      onClick={() => toggleReviewTag(def.key)}
                    >
                      {tagDefinitionLabel(def)}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}

          <textarea
            className="variant-review-textarea"
            value={note}
            onChange={(event) => {
              markEdited();
              setNote(event.target.value);
            }}
            rows={3}
            placeholder="Classification rationale / note"
          />
        </div>

        <div className="variant-search-actions variant-review-modal-actions">
          <button
            type="button"
            className="button-secondary"
            onClick={() => {
              markEdited();
              setSelections({});
            }}
          >
            Clear all
          </button>
          <button
            type="button"
            className="form-button"
            onClick={() => {
              void handleSave();
            }}
            disabled={isPending}
          >
            {isPending ? 'Saving…' : 'Save classification'}
          </button>
        </div>
      </div>

      {tip ? (
        <div
          className="acmg-tooltip"
          role="tooltip"
          style={
            tip.below
              ? { left: tip.left, top: tip.top }
              : { left: tip.left, bottom: window.innerHeight - tip.top }
          }
        >
          <p className="acmg-tooltip-title">
            {tip.def.code} · {tip.def.name}
          </p>
          <p className="acmg-tooltip-body">{tip.selection?.evidence ?? tip.def.description}</p>
          {tip.selection?.contraindicated ? (
            <p className="acmg-tooltip-against">Data argues against applying this criterion.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

// Tooltip state for the custom hover popover.
type TipState = {
  def: AcmgCriterionDef;
  selection?: AcmgSelection;
  left: number;
  top: number;
  below: boolean;
};

// Tier label per ACMG code family (PVS/PS/PM/PP, BA/BS/BP).
const TIER_LABELS: Record<string, string> = {
  PVS: 'Very strong',
  PS: 'Strong',
  PM: 'Moderate',
  PP: 'Supporting',
  BA: 'Stand-alone',
  BS: 'Strong',
  BP: 'Supporting',
};

const codeFamily = (code: string): string => code.replace(/[0-9].*$/, '');

type FamilyGroupDef = {
  family: string;
  direction: 'pathogenic' | 'benign';
  items: AcmgCriterionDef[];
};

function groupFamilies(criteria: AcmgCriterionDef[]): FamilyGroupDef[] {
  const groups: FamilyGroupDef[] = [];
  for (const def of criteria) {
    const family = codeFamily(def.code);
    const last = groups[groups.length - 1];
    if (last && last.family === family) {
      last.items.push(def);
    } else {
      groups.push({ family, direction: def.direction, items: [def] });
    }
  }
  return groups;
}

// One column per ACMG code family, ordered left→right to mirror the green→red
// scale: benign (BA, BS, BP) on the left, then pathogenic from weakest to
// strongest (PP, PM, PS, PVS) so very-strong pathogenic sits on the far right.
function buildFamilyGroups(): FamilyGroupDef[] {
  const benign = groupFamilies(BENIGN_CRITERIA); // BA, BS, BP
  const pathogenic = groupFamilies(PATHOGENIC_CRITERIA).reverse(); // PP, PM, PS, PVS
  return [...benign, ...pathogenic];
}

const FAMILY_GROUPS = buildFamilyGroups();

type FamilyGroupProps = {
  group: FamilyGroupDef;
  selections: Record<string, AcmgSelection>;
  onToggle: (code: AcmgCriterionCode, patch: Partial<AcmgSelection>) => void;
  onShowTip: (def: AcmgCriterionDef, selection: AcmgSelection | undefined, el: HTMLElement) => void;
  onHideTip: () => void;
};

function FamilyGroup({ group, selections, onToggle, onShowTip, onHideTip }: FamilyGroupProps) {
  return (
    <div className={`acmg-family acmg-family--${group.direction}`}>
      <div className="acmg-tier-label">
        {group.family} · {TIER_LABELS[group.family] ?? ''}
      </div>
      <div className="acmg-criteria-list">
        {group.items.map((def) => (
          <CriterionRow
            key={def.code}
            def={def}
            selection={selections[def.code]}
            onToggle={onToggle}
            onShowTip={onShowTip}
            onHideTip={onHideTip}
          />
        ))}
      </div>
    </div>
  );
}

type CriterionRowProps = {
  def: AcmgCriterionDef;
  selection?: AcmgSelection;
  onToggle: (code: AcmgCriterionCode, patch: Partial<AcmgSelection>) => void;
  onShowTip: (def: AcmgCriterionDef, selection: AcmgSelection | undefined, el: HTMLElement) => void;
  onHideTip: () => void;
};

function CriterionRow({ def, selection, onToggle, onShowTip, onHideTip }: CriterionRowProps) {
  const accepted = selection?.accepted ?? false;
  const strength = selection?.strength ?? def.defaultStrength;
  const suggested = (selection?.autoSuggested ?? false) && !accepted;
  const contraindicated = selection?.contraindicated ?? false;
  // Ruled out by the variant class — greyed as a hint, but still overridable.
  const notApplicable = (selection?.notApplicable ?? false) && !accepted;

  return (
    <div
      className={[
        'acmg-criterion',
        def.direction === 'benign' ? 'acmg-criterion--benign' : '',
        accepted ? 'acmg-criterion--accepted' : '',
        suggested ? 'acmg-criterion--suggested' : '',
        contraindicated && !accepted ? 'acmg-criterion--against' : '',
        notApplicable ? 'acmg-criterion--na' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      onMouseEnter={(event) => onShowTip(def, selection, event.currentTarget)}
      onMouseLeave={onHideTip}
    >
      <label className="analysis-checkbox">
        <input
          type="checkbox"
          checked={accepted}
          aria-label={`${def.code}: ${def.name}`}
          onChange={(event) => onToggle(def.code, { accepted: event.target.checked })}
          onFocus={(event) => onShowTip(def, selection, event.currentTarget.closest('.acmg-criterion') as HTMLElement)}
          onBlur={onHideTip}
        />
      </label>
      <span className="acmg-criterion-code">{def.code}</span>
      {accepted ? null : suggested ? (
        <span className="acmg-criterion-flag acmg-criterion-flag--consider" aria-label="Consider">
          ●
        </span>
      ) : contraindicated ? (
        <span className="acmg-criterion-flag acmg-criterion-flag--against" aria-label="Data argues against">
          ✕
        </span>
      ) : notApplicable ? (
        <span className="acmg-criterion-flag acmg-criterion-flag--na" aria-label="Not applicable">
          n/a
        </span>
      ) : null}
      <select
        className="acmg-criterion-strength"
        value={strength}
        aria-label={`${def.code} strength`}
        disabled={def.allowedStrengths.length <= 1}
        onChange={(event) => onToggle(def.code, { strength: event.target.value as AcmgStrength })}
      >
        {def.allowedStrengths.map((option) => (
          <option key={option} value={option}>
            {STRENGTH_LABELS[option]}
          </option>
        ))}
      </select>
    </div>
  );
}
