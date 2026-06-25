import React, { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  COLLABORATION_QUICK_TAGS,
  getTagDefinitionMap,
  type FamilyMember,
  type SmallVariant,
  type SmallVariantTranscript,
  type SmallVariantTagDefinition,
} from './smallVariantSearch';
import {
  buildReviewTagTooltip,
  buildSmallVariantExternalLinks,
  buildSmallVariantGeneInfoHref,
  buildSmallVariantNavigation,
  formatFrequency,
  formatCompoundHetPhaseStatus,
  formatLocus,
  formatScore,
  formatTokenLabel,
  getClinvarHighlightTone,
  getClinvarTone,
  getImpactTone,
  getReviewClassificationTone,
  getReviewTagStyle,
  sortReviewTagKeys,
} from './smallVariantResultUtils';
import SvSecondHitBadge from './SvSecondHitBadge';
import VariantPriorityBlock from './VariantPriorityBlock';
import NiptClassificationBlock from './NiptClassificationBlock';

interface SmallVariantCardsProps {
  variants: SmallVariant[];
  members: FamilyMember[];
  familyId?: string;
  projectId?: string;
  locationSearch: string;
  speciesName?: string;
  assemblyName?: string;
  assemblyVersion?: string;
  tags: SmallVariantTagDefinition[];
  reviewIsPending?: boolean;
  onEditReview: (variant: SmallVariant) => void;
  onAcmgClassify: (variant: SmallVariant) => void;
  onToggleReviewTag: (variant: SmallVariant, tagKey: string) => Promise<void>;
}

const transcriptSourceFor = (transcriptId?: string | null) => {
  const normalized = String(transcriptId || '').trim().toUpperCase();
  if (!normalized) return null;
  if (normalized.startsWith('ENST')) return 'Ensembl';
  if (
    normalized.startsWith('NM_') ||
    normalized.startsWith('NR_') ||
    normalized.startsWith('XM_') ||
    normalized.startsWith('XR_') ||
    normalized.startsWith('NG_')
  ) {
    return 'RefSeq';
  }
  return 'Other';
};

const limitTranscriptFlagBySource = (
  transcripts: SmallVariantTranscript[],
  flag: 'mane_select' | 'mane_plus_clinical',
): SmallVariantTranscript[] => {
  const flagged = transcripts
    .map((transcript, index) => ({ transcript, index }))
    .filter((entry) => Boolean(entry.transcript[flag]));
  if (flagged.length <= 2) return transcripts;

  const prioritized = [...flagged].sort((left, right) => {
    const leftPrimary = left.transcript.primary ? 1 : 0;
    const rightPrimary = right.transcript.primary ? 1 : 0;
    if (leftPrimary !== rightPrimary) return rightPrimary - leftPrimary;
    const leftImpact = String(left.transcript.impact || '').toUpperCase();
    const rightImpact = String(right.transcript.impact || '').toUpperCase();
    const impactRank = (value: string) => {
      if (value === 'HIGH') return 3;
      if (value === 'MODERATE' || value === 'MEDIUM') return 2;
      if (value === 'LOW') return 1;
      return 0;
    };
    const leftRank = impactRank(leftImpact);
    const rightRank = impactRank(rightImpact);
    if (leftRank !== rightRank) return rightRank - leftRank;
    return left.index - right.index;
  });

  const keep = new Set<number>();
  const seenSources = new Set<string>();
  for (const entry of prioritized) {
    const source = (
      entry.transcript.transcript_source ||
      transcriptSourceFor(entry.transcript.transcript_id) ||
      'Other'
    ).toUpperCase();
    if (seenSources.has(source)) continue;
    seenSources.add(source);
    keep.add(entry.index);
    if (keep.size >= 2) break;
  }
  if (!keep.size) keep.add(prioritized[0].index);

  return transcripts.map((transcript, index) =>
    keep.has(index) ? transcript : { ...transcript, [flag]: false },
  );
};

const normalizeTranscriptFlags = (transcripts: SmallVariantTranscript[]) => {
  let normalized = limitTranscriptFlagBySource(transcripts, 'mane_select');
  normalized = limitTranscriptFlagBySource(normalized, 'mane_plus_clinical');
  return normalized;
};

const getVariantTranscripts = (variant: SmallVariant): SmallVariantTranscript[] => {
  const listed = (variant.transcripts || []).filter(
    (transcript) =>
      transcript.transcript_id ||
      transcript.hgvsc ||
      transcript.hgvsp ||
      transcript.effect ||
      transcript.impact,
  );
  const hasPrimaryFields = Boolean(
    variant.transcript_id ||
      variant.hgvsc ||
      variant.hgvsp ||
      variant.effect ||
      variant.impact,
  );
  if (!hasPrimaryFields) {
    return listed.length ? normalizeTranscriptFlags(listed) : [];
  }
  // The primary transcript is delivered as the flat variant fields and omitted
  // from `transcripts` (so it isn't serialized twice); reconstruct it and lead the
  // list with it, dropping any listed entry that duplicates it (older payloads
  // still included the primary inline).
  const primary: SmallVariantTranscript = {
    gene: variant.gene,
    gene_id: variant.gene_id,
    transcript_id: variant.transcript_id,
    transcript_source: transcriptSourceFor(variant.transcript_id),
    feature_type: variant.feature_type,
    transcript_biotype: variant.transcript_biotype,
    impact: variant.impact,
    effect: variant.effect,
    hgvsc: variant.hgvsc,
    hgvsp: variant.hgvsp,
    exon: variant.exon,
    intron: variant.intron,
    canonical: variant.canonical,
    mane_select: variant.mane_select,
    mane_plus_clinical: variant.mane_plus_clinical,
    primary: true,
  };
  const others = listed.filter(
    (transcript) => !transcript.primary && transcript.transcript_id !== variant.transcript_id,
  );
  return normalizeTranscriptFlags([primary, ...others]);
};

const transcriptRegionLabel = (transcript: SmallVariantTranscript) =>
  transcript.exon ? `Exon ${transcript.exon}` : transcript.intron ? `Intron ${transcript.intron}` : '—';

const transcriptBadges = (transcript: SmallVariantTranscript) =>
  [
    transcript.mane_select ? { label: 'MANE Select', tone: 'success' } : null,
    transcript.mane_plus_clinical ? { label: 'MANE Plus Clinical', tone: 'accent' } : null,
    transcript.canonical ? { label: 'Canonical', tone: 'warning' } : null,
    transcript.primary ? { label: 'Shown on card', tone: 'neutral' } : null,
  ].filter(
    (value): value is { label: string; tone: 'success' | 'accent' | 'warning' | 'neutral' } =>
      Boolean(value),
  );

const TranscriptPopup = ({
  variant,
  onClose,
}: {
  variant: SmallVariant;
  onClose: () => void;
}) => {
  const transcripts = getVariantTranscripts(variant);
  const geneName = variant.gene || variant.gene_id || transcripts[0]?.gene || 'Intergenic variant';
  const variantLabel = `${formatLocus(variant)} · ${variant.ref || '—'} → ${variant.alt || '—'}`;

  return (
    <div className="modal-backdrop" role="presentation" onClick={onClose}>
      <div
        className="modal-surface surface-card variant-transcript-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="small-variant-transcript-title"
        onClick={(event) => event.stopPropagation()}
      >
        <div className="variant-review-modal-header">
          <div className="variant-review-modal-summary">
            <p className="page-kicker">Transcripts</p>
            <h2 id="small-variant-transcript-title" className="catalog-card-title">
              {geneName}
            </h2>
            <p className="variant-review-modal-subtitle">{variantLabel}</p>
          </div>
          <button type="button" className="button-secondary" onClick={onClose}>
            Close
          </button>
        </div>

        <div className="variant-transcript-summary-grid">
          <div>
            <span>Gene</span>
            <strong>{geneName}</strong>
          </div>
          <div>
            <span>Variant</span>
            <strong>{variant.hgvsp || variant.hgvsc || variantLabel}</strong>
          </div>
          <div>
            <span>Transcript effects</span>
            <strong>{transcripts.length}</strong>
          </div>
        </div>

        <div className="data-table-shell variant-transcript-table-shell">
          <table className="analysis-table variant-transcript-table">
            <thead>
              <tr>
                <th>Transcript</th>
                <th>HGVS</th>
                <th>Effect</th>
                <th>Impact</th>
                <th>Exon / intron</th>
                <th>Flags</th>
              </tr>
            </thead>
            <tbody>
              {transcripts.map((transcript, index) => {
                const badges = transcriptBadges(transcript);
                return (
                  <tr key={`${transcript.transcript_id || 'transcript'}-${index}`}>
                    <td>
                      <div className="variant-transcript-id">
                        <strong>{transcript.transcript_id || '—'}</strong>
                        <span>
                          {transcript.transcript_source ||
                            transcriptSourceFor(transcript.transcript_id) ||
                            'Transcript'}
                          {transcript.transcript_biotype ? ` · ${transcript.transcript_biotype}` : ''}
                        </span>
                      </div>
                    </td>
                    <td>
                      <div className="variant-transcript-hgvs">
                        <span>{transcript.hgvsc || '—'}</span>
                        <span>{transcript.hgvsp || '—'}</span>
                      </div>
                    </td>
                    <td>{formatTokenLabel(transcript.effect || undefined)}</td>
                    <td>{transcript.impact || '—'}</td>
                    <td>{transcriptRegionLabel(transcript)}</td>
                    <td>
                      {badges.length ? (
                        <div className="variant-transcript-badges">
                          {badges.map((badge) => (
                            <span
                              key={badge.label}
                              className={`variant-card-chip variant-card-chip--${badge.tone}`}
                            >
                              {badge.label}
                            </span>
                          ))}
                        </div>
                      ) : (
                        <span className="table-empty">—</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
};

// Detail-disclosure annotations, built only when a card's "More annotations"
// section is open (see openCardIds) so collapsed cards skip this per-render work.
const buildVariantDetailData = (
  variant: SmallVariant,
  ctx: { locus: string; transcripts: SmallVariantTranscript[]; transcriptLabel: string },
) => {
  const { locus, transcripts, transcriptLabel } = ctx;
  const populationFrequencies = Object.entries(variant.population_frequencies || {}).filter(
    ([, value]) => typeof value === 'number' && Number.isFinite(value),
  );
  const additionalAnnotations = Object.entries(variant.annotation_extra || {}).filter(
    ([, value]) => value !== null && value !== '',
  );
  const scoreItems = [
    typeof variant.gene_pli === 'number'
      ? { label: 'pLI', value: formatScore(variant.gene_pli, 3) }
      : null,
    typeof variant.gene_missense_z === 'number'
      ? { label: 'Missense Z', value: formatScore(variant.gene_missense_z) }
      : null,
    variant.cadd_phred ? { label: 'CADD', value: formatScore(variant.cadd_phred) } : null,
    variant.revel ? { label: 'REVEL', value: formatScore(variant.revel) } : null,
    variant.spliceai_max ? { label: 'SpliceAI', value: formatScore(variant.spliceai_max) } : null,
    variant.sift ? { label: 'SIFT', value: variant.sift } : null,
    variant.polyphen ? { label: 'PolyPhen', value: variant.polyphen } : null,
    variant.lof_filter ? { label: 'LoF filter', value: variant.lof_filter } : null,
    variant.lof_flags ? { label: 'LoF flags', value: variant.lof_flags } : null,
  ].filter((item): item is { label: string; value: string } => Boolean(item));
  const frequencyItems = [
    typeof variant.gnomad_hom_count === 'number'
      ? { label: 'gnomAD hom', value: String(variant.gnomad_hom_count) }
      : null,
    ...populationFrequencies
      .filter(([label]) => label !== 'gnomad_af')
      .map(([label, value]) => ({
        label: label.replace(/_/g, ' '),
        value: formatFrequency(value),
      })),
  ].filter((item): item is { label: string; value: string } => Boolean(item));
  const transcriptItems = [
    { label: 'Transcript', value: transcripts.length ? transcriptLabel : '—' },
    { label: 'Gene ID', value: variant.gene_id || '—' },
    { label: 'Biotype', value: variant.transcript_biotype || '—' },
    { label: 'Feature', value: variant.feature_type || '—' },
    { label: 'Exon / intron', value: variant.exon || variant.intron || '—' },
  ];
  const changeItems = [
    { label: 'Consequence', value: formatTokenLabel(variant.effect) },
    { label: 'HGVS.c', value: variant.hgvsc || '—' },
    { label: 'HGVS.p', value: variant.hgvsp || '—' },
    { label: 'Locus', value: locus },
    { label: 'dbSNP', value: variant.rsid || '—' },
    { label: 'Alleles', value: `${variant.ref || '—'} → ${variant.alt || '—'}` },
    {
      label: 'Type / source',
      value: `${variant.type}${variant.source ? ` · ${variant.source}` : ''}`,
    },
    { label: 'Phase set', value: String(variant.ps ?? '—') },
  ];
  return { scoreItems, frequencyItems, transcriptItems, changeItems, additionalAnnotations };
};

export default function SmallVariantCards({
  variants,
  members,
  familyId,
  projectId,
  locationSearch,
  speciesName,
  assemblyName,
  assemblyVersion,
  tags,
  reviewIsPending = false,
  onEditReview,
  onAcmgClassify,
  onToggleReviewTag,
}: SmallVariantCardsProps) {
  const tagMap = getTagDefinitionMap(tags);
  const [transcriptPopupVariant, setTranscriptPopupVariant] = useState<SmallVariant | null>(null);
  // Tracks which cards have the "More annotations" disclosure open, so the heavy
  // detail arrays are built only for those (collapsed is the default).
  const [openCardIds, setOpenCardIds] = useState<Set<string>>(() => new Set());
  const handleCardToggle = (variantId: string, open: boolean) => {
    setOpenCardIds((prev) => {
      if (open === prev.has(variantId)) return prev;
      const next = new Set(prev);
      if (open) next.add(variantId);
      else next.delete(variantId);
      return next;
    });
  };

  return (
    <div className="variant-card-list">
      {variants.map((variant) => {
        const { locus, igvHref, viewHref } = buildSmallVariantNavigation({
          variant,
          familyId,
          locationSearch,
          projectId,
        });
        const geneInfoHref = buildSmallVariantGeneInfoHref(variant, familyId, projectId);
        const externalLinks = buildSmallVariantExternalLinks({
          variant,
          speciesName,
          assemblyName,
          assemblyVersion,
        });
        const transcripts = getVariantTranscripts(variant);
        const transcriptLabel = variant.transcript_id || transcripts[0]?.transcript_id || 'View transcripts';
        const detailsOpen = openCardIds.has(variant._id);
        const detailData = detailsOpen
          ? buildVariantDetailData(variant, { locus, transcripts, transcriptLabel })
          : null;
        const {
          scoreItems = [],
          frequencyItems = [],
          transcriptItems = [],
          changeItems = [],
          additionalAnnotations = [],
        } = detailData ?? {};
        const clinvarSummary = formatTokenLabel(variant.clinvar);
        const sortedReviewTags = sortReviewTagKeys(variant.review?.tags || [], tagMap);
        const hasReviewTag = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.review);
        const isExcluded = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.excluded);
        const isReported = sortedReviewTags.includes(COLLABORATION_QUICK_TAGS.report);
        const visibleReviewTags = sortedReviewTags.filter(
          (tagKey) =>
            tagKey !== COLLABORATION_QUICK_TAGS.review &&
            tagKey !== COLLABORATION_QUICK_TAGS.excluded &&
            tagKey !== COLLABORATION_QUICK_TAGS.report,
        );

        const consequenceLabel = formatTokenLabel(variant.effect);
        const popFreq = variant.population_frequencies || {};
        const extra = variant.annotation_extra || {};
        const pickExtra = (keys: string[]): string | number | boolean | null => {
          for (const key of keys) {
            const value = extra[key];
            if (value !== undefined && value !== null && value !== '') return value;
          }
          return null;
        };
        const alphaMissense = pickExtra([
          'alphamissense',
          'alphamissense_score',
          'am_pathogenicity',
          'alphamissense_pathogenicity',
          'am_class',
          'alphamissense_class',
        ]);
        const gnomadLink = externalLinks.find((link) => link.label === 'gnomAD');
        const clinvarLink = externalLinks.find((link) => link.label === 'ClinVar');
        const omimLink = externalLinks.find((link) => link.label === 'OMIM');
        const decipherLink = externalLinks.find((link) => link.label === 'DECIPHER');
        const freqRows = [
          { label: 'gnomAD', value: formatFrequency(variant.gnomad_af), href: gnomadLink?.href },
          { label: 'popmax', value: formatFrequency(popFreq.gnomad_popmax_af), href: undefined },
          { label: 'exomes', value: formatFrequency(popFreq.gnomad_exomes_af), href: undefined },
          { label: 'genomes', value: formatFrequency(popFreq.gnomad_genomes_af), href: undefined },
          { label: 'TOPMed', value: formatFrequency(popFreq.topmed_af), href: undefined },
          ...(typeof variant.gnomad_hom_count === 'number'
            ? [{ label: 'gnomAD hom', value: String(variant.gnomad_hom_count), href: undefined }]
            : []),
        ].filter((row) => row.label === 'gnomAD' || row.value !== '—');
        const silicoRows = [
          typeof variant.cadd_phred === 'number' ? { label: 'CADD', value: formatScore(variant.cadd_phred) } : null,
          typeof variant.revel === 'number' ? { label: 'REVEL', value: formatScore(variant.revel) } : null,
          alphaMissense != null
            ? {
                label: 'AlphaMissense',
                value: typeof alphaMissense === 'number' ? formatScore(alphaMissense, 3) : String(alphaMissense),
              }
            : null,
          typeof variant.spliceai_max === 'number' ? { label: 'SpliceAI', value: formatScore(variant.spliceai_max) } : null,
          variant.sift ? { label: 'SIFT', value: variant.sift } : null,
          variant.polyphen ? { label: 'PolyPhen', value: variant.polyphen } : null,
          typeof variant.gene_pli === 'number' ? { label: 'pLI', value: formatScore(variant.gene_pli, 3) } : null,
          typeof variant.gene_missense_z === 'number' ? { label: 'Missense Z', value: formatScore(variant.gene_missense_z) } : null,
        ].filter((item): item is { label: string; value: string } => Boolean(item));
        const ic = variant.internal_cohort;

        return (
          <article
            key={variant._id}
            className={`variant-card variant-card--clinvar-${getClinvarHighlightTone(
              variant.clinvar,
            )}${isExcluded ? ' variant-card--excluded' : ''}`}
          >
            <div className="variant-card-head">
              <div className="variant-card-headline">
                {geneInfoHref ? (
                  <Link to={geneInfoHref} className="variant-card-gene">
                    {variant.gene || variant.gene_id || 'Intergenic variant'}
                  </Link>
                ) : (
                  <span className="variant-card-gene">
                    {variant.gene || variant.gene_id || 'Intergenic variant'}
                  </span>
                )}
                <span className="variant-card-locus">{formatLocus(variant)}</span>
                {variant.sv_second_hit ? <SvSecondHitBadge hit={variant.sv_second_hit} /> : null}
                <span className="variant-card-subtitle">
                  {variant.hgvsp || variant.hgvsc || consequenceLabel}
                </span>
              </div>
              <div className="variant-card-headtags">
                {variant.clinvar ? (
                  clinvarLink ? (
                    <a
                      href={clinvarLink.href}
                      target="_blank"
                      rel="noreferrer"
                      className={`variant-card-chip variant-card-chip--link variant-card-chip--${getClinvarTone(
                        variant.clinvar,
                      )}`}
                    >
                      <span className="variant-card-chip-key">ClinVar</span>
                      {clinvarSummary}
                    </a>
                  ) : (
                    <span className={`variant-card-chip variant-card-chip--${getClinvarTone(variant.clinvar)}`}>
                      <span className="variant-card-chip-key">ClinVar</span>
                      {clinvarSummary}
                    </span>
                  )
                ) : null}
                {variant.review?.classification ? (
                  <span
                    className={`variant-card-chip variant-card-chip--${getReviewClassificationTone(
                      variant.review.classification,
                    )}`}
                  >
                    {variant.review.classification}
                  </span>
                ) : null}
              </div>
            </div>

            {variant.nipt ? <NiptClassificationBlock nipt={variant.nipt} /> : null}

            {variant.priority ? <VariantPriorityBlock priority={variant.priority} /> : null}

            {visibleReviewTags.length || variant.review?.compound_het ? (
              <div className="variant-card-chip-row variant-card-tag-row">
                {variant.review?.compound_het ? (
                  <span className="variant-card-chip variant-card-chip--impact">Comp-het pair</span>
                ) : null}
                {visibleReviewTags.map((tagKey) => (
                  <span
                    key={tagKey}
                    className="variant-card-chip variant-card-chip--tag"
                    style={getReviewTagStyle(tagKey, tagMap)}
                    title={buildReviewTagTooltip({
                      tagKey,
                      tagMap,
                      tagMetadata: variant.review?.tag_metadata,
                    })}
                  >
                    {tagMap[tagKey]?.label || tagKey}
                  </span>
                ))}
              </div>
            ) : null}

            <div className="variant-card-cols">
              <section className="variant-card-col">
                <p className="variant-card-col-title">Variant &amp; genotypes</p>
                <div className="variant-card-chip-row">
                  <span className={`variant-card-chip variant-card-chip--${getImpactTone(variant.impact)}`}>
                    {consequenceLabel !== '—' ? consequenceLabel : variant.impact || 'Impact n/a'}
                  </span>
                  {variant.mane_select ? (
                    <span className="variant-card-chip variant-card-chip--success">MANE</span>
                  ) : null}
                  {variant.canonical ? (
                    <span className="variant-card-chip variant-card-chip--soft">Canonical</span>
                  ) : null}
                  {variant.lof ? (
                    <span className="variant-card-chip variant-card-chip--critical">LoF {variant.lof}</span>
                  ) : null}
                </div>
                <dl className="variant-card-mini-dl">
                  <div>
                    <dt>HGVS.c</dt>
                    <dd>{variant.hgvsc || '—'}</dd>
                  </div>
                  <div>
                    <dt>HGVS.p</dt>
                    <dd>{variant.hgvsp || '—'}</dd>
                  </div>
                  <div>
                    <dt>Alleles</dt>
                    <dd>
                      {variant.ref || '—'} → {variant.alt || '—'}
                    </dd>
                  </div>
                  <div>
                    <dt>dbSNP</dt>
                    <dd>{variant.rsid || '—'}</dd>
                  </div>
                  {omimLink ? (
                    <div>
                      <dt>OMIM</dt>
                      <dd>
                        <a
                          href={omimLink.href}
                          target="_blank"
                          rel="noreferrer"
                          className="variant-card-resource variant-card-resource--clinical"
                        >
                          {variant.gene || 'search'}
                        </a>
                      </dd>
                    </div>
                  ) : null}
                  {decipherLink ? (
                    <div>
                      <dt>DECIPHER</dt>
                      <dd>
                        <a
                          href={decipherLink.href}
                          target="_blank"
                          rel="noreferrer"
                          className="variant-card-resource variant-card-resource--clinical"
                        >
                          {variant.gene || 'view'}
                        </a>
                      </dd>
                    </div>
                  ) : null}
                </dl>
                {members.length ? (
                  <div className="variant-card-gtlist">
                    <div className="variant-card-gthead">
                      <span>Sample</span>
                      <span>Role</span>
                      <span>GT</span>
                      <span>DP</span>
                      <span>AD</span>
                      <span>AF</span>
                    </div>
                    {members.map((member) => {
                      const genotype = variant.genotypes.find(
                        (entry) => entry.sample === member.sample_id,
                      );
                      const gt = genotype?.gt || '—';
                      const depth = genotype?.dp;
                      const alleleDepths = genotype?.ad;
                      const alleleFrequencies = genotype?.af;
                      const refDepth =
                        alleleDepths && alleleDepths.length > 0 ? alleleDepths[0] : undefined;
                      const altDepths =
                        alleleDepths && alleleDepths.length > 1 ? alleleDepths.slice(1) : undefined;
                      const computedAfs =
                        alleleFrequencies && alleleFrequencies.length
                          ? alleleFrequencies
                          : typeof depth === 'number' && depth > 0 && altDepths?.length
                            ? altDepths
                                .map((value) => (typeof value === 'number' ? value / depth : undefined))
                                .filter((value): value is number => typeof value === 'number')
                            : undefined;
                      const adText =
                        typeof refDepth === 'number' || altDepths?.length
                          ? `${typeof refDepth === 'number' ? refDepth : '-'},${
                              altDepths ? altDepths.join(',') : '-'
                            }`
                          : '—';
                      const afText = computedAfs?.length
                        ? computedAfs
                            .map((value) => (Number.isFinite(value) ? value.toFixed(2) : '-'))
                            .join(',')
                        : '—';
                      const normalized = gt.replace(/\|/g, '/');
                      const zygosity =
                        !gt || gt === '—' || normalized === './.'
                          ? 'na'
                          : normalized === '0/0'
                            ? 'ref'
                            : normalized === '1/1'
                              ? 'hom'
                              : 'het';
                      const sexSymbol =
                        member.sex === 'male' ? '♂' : member.sex === 'female' ? '♀' : '⚥';
                      return (
                        <div
                          key={member.sample_id}
                          className={`variant-card-gtline variant-card-gtline--${zygosity}${
                            member.affected ? ' is-affected' : ''
                          }${member.role === 'proband' ? ' is-proband' : ''}`}
                        >
                          <span className="variant-card-gtline-sample">
                            <span
                              className={`variant-card-gtline-sex variant-card-gtline-sex--${
                                member.sex || 'und'
                              }`}
                              title={member.sex || 'unknown sex'}
                            >
                              {sexSymbol}
                            </span>
                            {member.sample_id}
                          </span>
                          <span className="variant-card-gtline-role">{member.role}</span>
                          <span className="variant-card-gtline-gt">{gt}</span>
                          <span className="variant-card-gtline-metric">
                            {typeof depth === 'number' ? depth : '—'}
                          </span>
                          <span className="variant-card-gtline-metric">{adText}</span>
                          <span className="variant-card-gtline-metric">{afText}</span>
                        </div>
                      );
                    })}
                  </div>
                ) : null}
              </section>

              <section className="variant-card-col">
                <p className="variant-card-col-title">Frequencies</p>
                <dl className="variant-card-mini-dl">
                  {freqRows.map((row) => (
                    <div key={row.label}>
                      <dt>{row.label}</dt>
                      <dd>
                        {row.href ? (
                          <a
                            href={row.href}
                            target="_blank"
                            rel="noreferrer"
                            className="variant-card-resource variant-card-resource--clinical"
                          >
                            {row.value}
                          </a>
                        ) : (
                          row.value
                        )}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="variant-card-coga">
                  <span className="variant-card-col-key">CoGA cohort</span>
                  {ic ? (
                    <span className="variant-card-coga-value">
                      {ic.samples} sample{ic.samples === 1 ? '' : 's'} · {ic.het} het / {ic.hom} hom
                      {' · '}
                      {ic.families} famil{ic.families === 1 ? 'y' : 'ies'}
                    </span>
                  ) : (
                    <span className="variant-card-empty-note">Not seen in your cohort</span>
                  )}
                </div>
              </section>

              <section className="variant-card-col">
                <p className="variant-card-col-title">In silico</p>
                {silicoRows.length ? (
                  <dl className="variant-card-mini-dl">
                    {silicoRows.map((item) => (
                      <div key={item.label}>
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                ) : (
                  <p className="variant-card-empty-note">No predictions imported.</p>
                )}
                {transcripts.length ? (
                  <button
                    type="button"
                    className="variant-transcript-trigger"
                    onClick={() => setTranscriptPopupVariant(variant)}
                  >
                    <span>{transcriptLabel}</span>
                    <span className="variant-transcript-trigger-count">
                      {transcripts.length} transcript{transcripts.length === 1 ? '' : 's'}
                    </span>
                  </button>
                ) : null}
              </section>
            </div>

            {variant.review?.note ? (
              <p className="variant-card-review-note">{variant.review.note}</p>
            ) : null}

            <div className="variant-card-footer">
              <div className="variant-card-quick">
                <button
                  type="button"
                  className={`variant-quick-toggle${hasReviewTag ? ' variant-quick-toggle--active' : ''}`}
                  disabled={reviewIsPending}
                  onClick={() => {
                    void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.review);
                  }}
                >
                  Review
                </button>
                <button
                  type="button"
                  className={`variant-quick-toggle${isExcluded ? ' variant-quick-toggle--active' : ''}`}
                  disabled={reviewIsPending}
                  onClick={() => {
                    void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.excluded);
                  }}
                >
                  Exclude
                </button>
                <button
                  type="button"
                  className={`variant-quick-toggle${isReported ? ' variant-quick-toggle--active' : ''}`}
                  disabled={reviewIsPending}
                  onClick={() => {
                    void onToggleReviewTag(variant, COLLABORATION_QUICK_TAGS.report);
                  }}
                >
                  Report
                </button>
                <button
                  type="button"
                  className="variant-review-link"
                  onClick={() => onEditReview(variant)}
                >
                  Tags &amp; notes
                </button>
                <button
                  type="button"
                  className="variant-review-link"
                  onClick={() => onAcmgClassify(variant)}
                >
                  ACMG classify
                </button>
              </div>
              <div className="variant-card-nav">
                <Link to={igvHref} className="variant-card-resource variant-card-resource--clinical">
                  IGV
                </Link>
                <Link to={viewHref} className="variant-card-resource variant-card-resource--clinical">
                  Chromosome view
                </Link>
              </div>
            </div>

            <details
              className="variant-card-more"
              onToggle={(event) => handleCardToggle(variant._id, event.currentTarget.open)}
            >
              <summary>More annotations &amp; detail</summary>
              {detailsOpen && (
              <div className="variant-card-more-grid">
                <div className="variant-card-section">
                  <p className="variant-card-section-title">Variant summary</p>
                  <dl className="variant-card-detail-list">
                    {changeItems.map((item) => (
                      <div key={item.label} className="variant-card-detail-row">
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                <div className="variant-card-section">
                  <p className="variant-card-section-title">Transcript context</p>
                  <dl className="variant-card-detail-list">
                    {transcriptItems.map((item) => (
                      <div key={item.label} className="variant-card-detail-row">
                        <dt>{item.label}</dt>
                        <dd>{item.value}</dd>
                      </div>
                    ))}
                  </dl>
                </div>
                <div className="variant-card-section">
                  <p className="variant-card-section-title">All scores &amp; constraint</p>
                  {scoreItems.length ? (
                    <dl className="variant-card-stat-list">
                      {scoreItems.map((item) => (
                        <div key={item.label} className="variant-card-stat-row">
                          <dt>{item.label}</dt>
                          <dd>{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="variant-card-empty-note">No predictive scores imported.</p>
                  )}
                </div>
                <div className="variant-card-section">
                  <p className="variant-card-section-title">All population frequencies</p>
                  {frequencyItems.length ? (
                    <dl className="variant-card-stat-list">
                      {frequencyItems.map((item) => (
                        <div key={item.label} className="variant-card-stat-row">
                          <dt>{item.label}</dt>
                          <dd>{item.value}</dd>
                        </div>
                      ))}
                    </dl>
                  ) : (
                    <p className="variant-card-empty-note">No population frequencies imported.</p>
                  )}
                </div>
                {additionalAnnotations.length ? (
                  <div className="variant-card-section">
                    <p className="variant-card-section-title">Additional annotations</p>
                    <dl className="variant-card-detail-list">
                      {additionalAnnotations.map(([label, value]) => (
                        <div key={label} className="variant-card-detail-row">
                          <dt>{label.replace(/_/g, ' ')}</dt>
                          <dd>{String(value)}</dd>
                        </div>
                      ))}
                    </dl>
                  </div>
                ) : null}
                {variant.review?.compound_het ? (
                  <div className="variant-card-section">
                    <p className="variant-card-section-title">Compound-het pair</p>
                    <div className="variant-compound-het-summary">
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Gene</span>
                        <span>
                          {variant.review.compound_het.gene ||
                            variant.review.compound_het.gene_id ||
                            variant.gene ||
                            variant.gene_id ||
                            'Compound het group'}
                        </span>
                      </div>
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Partner</span>
                        <span>{variant.review.compound_het.partner_variant_ids.join(', ')}</span>
                      </div>
                      <div className="variant-compound-het-summary-row">
                        <span className="analysis-pill analysis-pill--muted">Phase</span>
                        <span>
                          {formatCompoundHetPhaseStatus(variant.review.compound_het.phase_status)}
                        </span>
                      </div>
                    </div>
                  </div>
                ) : null}
              </div>
              )}
            </details>
          </article>
        );
      })}
      {transcriptPopupVariant ? (
        <TranscriptPopup
          variant={transcriptPopupVariant}
          onClose={() => setTranscriptPopupVariant(null)}
        />
      ) : null}
    </div>
  );
}
