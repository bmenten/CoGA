import type { StructuralVariant } from './structuralVariantSearch';

/**
 * Flanking context added on each side of a structural variant when it is opened in a
 * genome workspace.
 *
 * The window deliberately spans the whole call — first breakpoint to last — plus this
 * much either side, so the SV is visible in one view together with the sequence around
 * both junctions. The previous window was the bare `start-end` span, which for a BND or
 * an insertion (where `end` is the same base as `start`) collapsed to a single position
 * and left IGV parked on the start breakpoint instead of showing the event.
 */
export const SV_FLANK_BP = 50_000;

/**
 * Primary-locus IGV + Chromosome-view links for a structural variant. Shared by
 * StructuralVariantCards and StructuralVariantTable so the locus/window/query-param
 * format stays in one place. The Table additionally renders a remote-locus (BND
 * partner) IGV link, which stays local to that component but pads with the same flank.
 */
export const buildStructuralVariantNavigation = ({
  familyId,
  linkSearch,
  projectId,
  variant,
}: {
  familyId?: string;
  linkSearch: string;
  projectId?: string;
  variant: StructuralVariant;
}) => {
  const chr = variant.chr.startsWith('chr') ? variant.chr : `chr${variant.chr}`;
  // `end` is not guaranteed to sit after `start` for every caller/type, so anchor the
  // far edge on whichever is greater before padding.
  const spanEnd = Math.max(variant.start, variant.end) + SV_FLANK_BP;
  const locus = `${chr}:${Math.max(1, variant.start - SV_FLANK_BP)}-${spanEnd}`;
  const backPath = `/families/${familyId}/structural-variants${linkSearch}`;
  const igvHref = `/families/${familyId}/igv?locus=${encodeURIComponent(locus)}${
    projectId ? `&project_id=${projectId}` : ''
  }&back_path=${encodeURIComponent(backPath)}`;
  const viewHref = `/families/${familyId}/chromosome/${variant.chr.replace(/^chr/, '')}?start=${Math.max(
    0,
    variant.start - SV_FLANK_BP,
  )}&end=${spanEnd}${linkSearch ? `&${linkSearch.slice(1)}` : ''}${
    projectId ? `&project_id=${projectId}` : ''
  }`;
  return { locus, igvHref, viewHref };
};
