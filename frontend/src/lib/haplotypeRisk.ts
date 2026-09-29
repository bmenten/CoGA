export type HaplotypeLane = 'hap1' | 'hap2';
export type HaplotypeOrigin = 'paternal' | 'maternal';
export type HaplotypeInheritanceMode =
  | 'dominant'
  | 'recessive'
  | 'x_linked_dominant'
  | 'x_linked_recessive'
  | 'unknown';
export type DiseaseHaplotypeKind = 'dominant' | 'recessive-maternal' | 'recessive-paternal' | 'x-linked';
export type HaplotypeRiskState = 'affected_or_at_risk' | 'carrier' | 'unaffected_non_carrier' | 'uninformative';

/**
 * Pedigree-aware colour class the backend assigns to each lane. When present it
 * overrides the role-based origin inference below — essential for relatives
 * (e.g. a grandparent) whose flat role does not encode their place in the
 * pedigree. ``untransmitted``/``unknown`` lanes carry no founder identity and are
 * rendered grey.
 */
export type HaplotypeLineage = 'paternal' | 'maternal' | 'untransmitted' | 'unknown';

export interface HaplotypeSegmentLike {
  chr?: string | null;
  start: number;
  end: number;
  hap1: string;
  hap2: string;
  ps?: number | null;
  hap1_lineage?: HaplotypeLineage | string | null;
  hap2_lineage?: HaplotypeLineage | string | null;
  /** A male carries one copy of this block: chrX or chrY outside the pseudo-autosomal regions
   * of the family's assembly, as the backend marks each block. Absent or false: two copies. */
  hemizygous_in_males?: boolean | null;
}

export interface HaplotypeSampleLike {
  sample: string;
  segments: HaplotypeSegmentLike[];
}

export interface HaplotypeMemberLike {
  sample_id: string;
  role?: string | null;
  affected?: boolean | null;
  sex?: string | null;
  clinical_status?: string | null;
  carrier_status?: string | boolean | null;
  carrier_type?: string | null;
}

export interface HaplotypeRiskRegion {
  chr?: string | null;
  start: number;
  end: number;
}

export interface HaplotypeSignature {
  origin: HaplotypeOrigin;
  value: string;
}

export interface DiseaseHaplotypeSignature extends HaplotypeSignature {
  kind: DiseaseHaplotypeKind;
}

export interface DiseaseHaplotypeModel {
  mode: HaplotypeInheritanceMode;
  signatures: DiseaseHaplotypeSignature[];
  informative: boolean;
}

export const normalizeHaplotypeChrom = (chrom?: string | null): string =>
  String(chrom || '')
    .trim()
    .replace(/^chr/i, '')
    .toUpperCase();

export const normalizeHaplotypeInheritance = (inheritanceModel?: string | null): HaplotypeInheritanceMode => {
  const normalized = String(inheritanceModel || '')
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, '_');
  if (!normalized) return 'unknown';
  if (['AD', 'AUTOSOMAL_DOMINANT', 'DOMINANT'].includes(normalized)) return 'dominant';
  if (['AR', 'AUTOSOMAL_RECESSIVE', 'RECESSIVE'].includes(normalized)) return 'recessive';
  if (['XLD', 'X_LINKED_DOMINANT', 'XL_DOMINANT'].includes(normalized)) {
    return 'x_linked_dominant';
  }
  if (['XLR', 'X_LINKED_RECESSIVE', 'XL_RECESSIVE', 'X_LINKED'].includes(normalized)) {
    return 'x_linked_recessive';
  }
  return 'unknown';
};

export const resolveHaplotypeInheritanceModel = (
  inheritanceModel: string | null | undefined,
  members: HaplotypeMemberLike[],
): string => {
  if (normalizeHaplotypeInheritance(inheritanceModel) !== 'unknown') {
    return String(inheritanceModel);
  }
  return members.some((member) => isCarrierStatus(member.carrier_status)) ? 'AR' : 'AD';
};

export const isCarrierStatus = (status: HaplotypeMemberLike['carrier_status']): boolean =>
  status === true || status === 'carrier';

const isKnownNonCarrier = (member: HaplotypeMemberLike): boolean =>
  member.carrier_status === 'not_carrier' || member.carrier_status === false;

const isAffectedMember = (member: HaplotypeMemberLike): boolean =>
  Boolean(member.affected) || member.clinical_status === 'affected';

const normalizeRole = (role?: string | null): string =>
  String(role || '')
    .trim()
    .toLowerCase();

const normalizeSex = (sex?: string | null): 'male' | 'female' | 'unknown' => {
  const normalized = String(sex || '')
    .trim()
    .toLowerCase();
  if (['male', 'm', '1'].includes(normalized)) return 'male';
  if (['female', 'f', '2'].includes(normalized)) return 'female';
  return 'unknown';
};

const isXChrom = (chrom?: string | null): boolean => normalizeHaplotypeChrom(chrom) === 'X';

/**
 * A male has one X in this block: chrX outside the pseudo-autosomal regions, where the backend
 * marks the block `hemizygous_in_males` from its PAR table for the family's assembly. In a PAR a
 * son also carries his father's copy (on the father's X or Y), so a block in or reaching into a
 * PAR, or one on an assembly whose PARs are not known, is read on both lanes.
 */
const isMaleWithOneX = (member: HaplotypeMemberLike, segment: HaplotypeSegmentLike, chrom?: string | null): boolean =>
  normalizeSex(member.sex) === 'male' && isXChrom(chrom || segment.chr) && segment.hemizygous_in_males === true;

const haplotypeValue = (segment: HaplotypeSegmentLike, lane: HaplotypeLane): string => String(segment[lane] ?? '');

const isInformativeHaplotypeValue = (value: string): boolean => value.trim() !== '' && value !== '.';

const selectMaleXVisibleLane = (member: HaplotypeMemberLike, segment: HaplotypeSegmentLike): HaplotypeLane => {
  const role = normalizeRole(member.role);
  const preferredLane: HaplotypeLane = role === 'father' ? 'hap1' : 'hap2';
  const fallbackLane: HaplotypeLane = preferredLane === 'hap1' ? 'hap2' : 'hap1';
  return isInformativeHaplotypeValue(haplotypeValue(segment, preferredLane)) ? preferredLane : fallbackLane;
};

export const getRenderableHaplotypeLanes = (
  member: HaplotypeMemberLike,
  segment: HaplotypeSegmentLike,
  chrom?: string | null,
): HaplotypeLane[] => {
  if (isMaleWithOneX(member, segment, chrom)) {
    return [selectMaleXVisibleLane(member, segment)];
  }
  return ['hap1', 'hap2'];
};

const laneLineage = (segment: HaplotypeSegmentLike, lane: HaplotypeLane): string =>
  String((lane === 'hap1' ? segment.hap1_lineage : segment.hap2_lineage) ?? '')
    .trim()
    .toLowerCase();

export const getHaplotypeLaneSignature = (
  member: HaplotypeMemberLike,
  segment: HaplotypeSegmentLike,
  lane: HaplotypeLane,
  chrom?: string | null,
): HaplotypeSignature | null => {
  const value = haplotypeValue(segment, lane);
  if (!isInformativeHaplotypeValue(value)) return null;

  // A pedigree-aware lineage tag, when present, is authoritative — it already
  // accounts for transmission through the pedigree, which `role` cannot.
  const lineage = laneLineage(segment, lane);
  if (lineage === 'paternal') return { origin: 'paternal', value };
  if (lineage === 'maternal') return { origin: 'maternal', value };
  if (lineage === 'untransmitted' || lineage === 'unknown') return null;

  const role = normalizeRole(member.role);
  let origin: HaplotypeOrigin;
  if (isMaleWithOneX(member, segment, chrom)) {
    origin = role === 'father' ? 'paternal' : 'maternal';
  } else if (role === 'father') {
    origin = 'paternal';
  } else if (role === 'mother') {
    origin = 'maternal';
  } else {
    origin = lane === 'hap1' ? 'paternal' : 'maternal';
  }
  return { origin, value };
};

const signatureKey = (signature: HaplotypeSignature): string => `${signature.origin}:${signature.value}`;

const signatureFromKey = (key: string): HaplotypeSignature | null => {
  const [origin, value] = key.split(':');
  if ((origin !== 'paternal' && origin !== 'maternal') || !value) return null;
  return { origin, value };
};

const segmentOverlapsRegion = (segment: HaplotypeSegmentLike, region: HaplotypeRiskRegion): boolean => {
  const segmentChrom = normalizeHaplotypeChrom(segment.chr);
  const regionChrom = normalizeHaplotypeChrom(region.chr);
  if (segmentChrom && regionChrom && segmentChrom !== regionChrom) return false;
  return segment.end >= region.start && segment.start <= region.end;
};

const buildSegmentMap = (samples: HaplotypeSampleLike[]): Map<string, HaplotypeSegmentLike[]> =>
  new Map(samples.map((sample) => [sample.sample, sample.segments || []]));

// A lane value names one of a parent's homologs only when it is a homolog label (0, 1, …).
// The trio block builder writes '?' for a side whose inherited homolog is not confirmed yet,
// and '.' (a deleted homolog) or '' (no value) name none either.
const isHomologLabel = (value: string): boolean => /^\d+$/.test(value);

interface HomologInRegion {
  signature: HaplotypeSignature;
  /** The extent of the block the homolog was seen in. */
  start: number;
  end: number;
}

/** The homologs a member carries in its blocks over the region, each with its block's extent.
 * A grey lane (untransmitted or unknown lineage) and a lane without a homolog label carry
 * none: there the member's haplotype is missing data, not a homolog it does not carry. */
const homologsInRegion = (
  member: HaplotypeMemberLike,
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
): HomologInRegion[] => {
  const homologs: HomologInRegion[] = [];
  const segments = segmentsBySample.get(member.sample_id) || [];
  segments.forEach((segment) => {
    if (!segmentOverlapsRegion(segment, region)) return;
    getRenderableHaplotypeLanes(member, segment, region.chr || segment.chr).forEach((lane) => {
      const signature = getHaplotypeLaneSignature(member, segment, lane, region.chr || segment.chr);
      if (!signature || !isHomologLabel(signature.value)) return;
      homologs.push({ signature, start: segment.start, end: segment.end });
    });
  });
  return homologs;
};

const carriedSignatureKeysForMember = (
  member: HaplotypeMemberLike,
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
  origin?: HaplotypeOrigin,
): Set<string> => {
  const keys = new Set<string>();
  homologsInRegion(member, segmentsBySample, region).forEach(({ signature }) => {
    if (origin && signature.origin !== origin) return;
    keys.add(signatureKey(signature));
  });
  return keys;
};

/**
 * Whether the member's blocks name a homolog of this parental side at every position of the
 * region. Block extents are read inclusively, like the overlap test above, and blocks that
 * abut (end + 1 = next start) are contiguous; any other gap, or a region only partly
 * reached, is not covered.
 */
const coversRegionOnSide = (
  homologs: HomologInRegion[],
  region: HaplotypeRiskRegion,
  origin: HaplotypeOrigin,
): boolean => {
  if (!(region.end >= region.start)) return false;
  const spans = homologs
    .filter((homolog) => homolog.signature.origin === origin)
    .sort((a, b) => a.start - b.start);
  let coveredTo = region.start - 1;
  for (const span of spans) {
    if (span.start > coveredTo + 1) return false;
    coveredTo = Math.max(coveredTo, span.end);
    if (coveredTo >= region.end) return true;
  }
  return false;
};

/** The one X a male member has, as the colouring labels it: his mother's, or, for the father
 * himself, the family's paternal side. */
const maleXOrigin = (member: HaplotypeMemberLike): HaplotypeOrigin =>
  normalizeRole(member.role) === 'father' ? 'paternal' : 'maternal';

const intersectCandidateKeys = (
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
  origin?: HaplotypeOrigin,
): string[] => {
  let intersection: Set<string> | null = null;
  for (const member of members) {
    const keys = carriedSignatureKeysForMember(member, segmentsBySample, region, origin);
    // A member that contributes no in-region signatures (e.g. a relative greyed by
    // the lineage service — unknown/untransmitted on both lanes, or off-region) is
    // simply non-informative for this intersection, NOT a hard zeroing event. The
    // previous `return []` let one greyed affected relative collapse an otherwise
    // resolvable disease call to uninformative (a missed clinical highlight).
    if (keys.size === 0) continue;
    if (!intersection) {
      intersection = new Set(keys);
      continue;
    }
    intersection = new Set(Array.from(intersection).filter((key) => keys.has(key)));
  }
  // Only when NO member contributed any signature is the call genuinely empty.
  return Array.from(intersection || []);
};

const removeKnownNonRiskCarriers = (
  candidates: string[],
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
): string[] => {
  const nonRiskMembers = members.filter((member) => !isAffectedMember(member) && isKnownNonCarrier(member));
  if (nonRiskMembers.length === 0) return candidates;
  return candidates.filter(
    (candidate) =>
      !nonRiskMembers.some((member) => carriedSignatureKeysForMember(member, segmentsBySample, region).has(candidate)),
  );
};

const uniqueSignature = (candidates: string[]): HaplotypeSignature | null => {
  if (candidates.length !== 1) return null;
  return signatureFromKey(candidates[0]);
};

const inferDominantSignature = (
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
): DiseaseHaplotypeSignature[] => {
  const informativeMembers = members.filter(
    (member) =>
      isAffectedMember(member) || (isCarrierStatus(member.carrier_status) && member.carrier_type === 'obligate'),
  );
  if (informativeMembers.length < 2) return [];
  const candidates = removeKnownNonRiskCarriers(
    intersectCandidateKeys(informativeMembers, segmentsBySample, region),
    members,
    segmentsBySample,
    region,
  );
  const signature = uniqueSignature(candidates);
  return signature ? [{ ...signature, kind: 'dominant' }] : [];
};

const inferRecessiveSideSignature = (
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
  origin: HaplotypeOrigin,
): HaplotypeSignature | null => {
  const affectedMembers = members.filter(isAffectedMember);
  if (affectedMembers.length === 0) return null;
  const candidates = intersectCandidateKeys(affectedMembers, segmentsBySample, region, origin);
  return uniqueSignature(candidates);
};

const inferRecessiveSignatures = (
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
): DiseaseHaplotypeSignature[] => {
  const paternal = inferRecessiveSideSignature(members, segmentsBySample, region, 'paternal');
  const maternal = inferRecessiveSideSignature(members, segmentsBySample, region, 'maternal');
  const signatures: DiseaseHaplotypeSignature[] = [];
  if (paternal) signatures.push({ ...paternal, kind: 'recessive-paternal' });
  if (maternal) signatures.push({ ...maternal, kind: 'recessive-maternal' });
  return signatures;
};

const inferXLinkedRecessiveSignatures = (
  members: HaplotypeMemberLike[],
  segmentsBySample: Map<string, HaplotypeSegmentLike[]>,
  region: HaplotypeRiskRegion,
): DiseaseHaplotypeSignature[] => {
  const affectedMales = members.filter((member) => isAffectedMember(member) && normalizeSex(member.sex) === 'male');
  if (affectedMales.length > 0) {
    const signature = uniqueSignature(intersectCandidateKeys(affectedMales, segmentsBySample, region));
    return signature ? [{ ...signature, kind: 'x-linked' }] : [];
  }

  const affectedFemales = members.filter((member) => isAffectedMember(member) && normalizeSex(member.sex) === 'female');
  const paternal = uniqueSignature(intersectCandidateKeys(affectedFemales, segmentsBySample, region, 'paternal'));
  const maternal = uniqueSignature(intersectCandidateKeys(affectedFemales, segmentsBySample, region, 'maternal'));
  const signatures: DiseaseHaplotypeSignature[] = [];
  if (paternal) signatures.push({ ...paternal, kind: 'x-linked' });
  if (maternal) signatures.push({ ...maternal, kind: 'x-linked' });
  return signatures;
};

export const inferDiseaseHaplotypes = ({
  samples,
  members,
  inheritanceModel,
  region,
}: {
  samples: HaplotypeSampleLike[];
  members: HaplotypeMemberLike[];
  inheritanceModel?: string | null;
  region: HaplotypeRiskRegion;
}): DiseaseHaplotypeModel => {
  const mode = normalizeHaplotypeInheritance(inheritanceModel);
  const segmentsBySample = buildSegmentMap(samples);
  let signatures: DiseaseHaplotypeSignature[] = [];

  if (mode === 'dominant') {
    signatures = inferDominantSignature(members, segmentsBySample, region);
  } else if (mode === 'recessive') {
    signatures = inferRecessiveSignatures(members, segmentsBySample, region);
  } else if (mode === 'x_linked_dominant') {
    signatures = inferDominantSignature(members, segmentsBySample, region).map((signature) => ({
      ...signature,
      kind: 'x-linked',
    }));
  } else if (mode === 'x_linked_recessive') {
    signatures = inferXLinkedRecessiveSignatures(members, segmentsBySample, region);
  }

  const uniqueSignatures = Array.from(
    new Map(signatures.map((signature) => [signatureKey(signature), signature])).values(),
  );
  return {
    mode,
    signatures: uniqueSignatures,
    informative: uniqueSignatures.length > 0,
  };
};

export const diseaseHaplotypeKindForLane = (
  model: DiseaseHaplotypeModel,
  member: HaplotypeMemberLike,
  segment: HaplotypeSegmentLike,
  lane: HaplotypeLane,
  chrom?: string | null,
): DiseaseHaplotypeKind | null => {
  const signature = getHaplotypeLaneSignature(member, segment, lane, chrom || segment.chr);
  if (!signature) return null;
  const matched = model.signatures.find(
    (riskSignature) => riskSignature.origin === signature.origin && riskSignature.value === signature.value,
  );
  return matched?.kind || null;
};

export interface HaplotypeRiskAssessment {
  state: HaplotypeRiskState;
  /** The call is uninformative because the member's own haplotype does not cover the ROI on a
   * parental side the call needs (missing data), not because no disease haplotype was resolved. */
  roiNotCovered: boolean;
  /** X-linked recessive, the member's sex not recorded, and the call a son would get differs
   * from a daughter's: both calls. The state then assumes neither sex: Affected / at risk when
   * either call is, otherwise uninformative. */
  sexDependent: { ifMale: HaplotypeRiskState; ifFemale: HaplotypeRiskState } | null;
}

/**
 * The member's risk state at the region, and whether an uninformative call is down to the
 * member's own missing data there.
 *
 * A positive call needs the risk haplotype seen anywhere in the region; that stays the
 * conservative direction. A negative call needs more than not finding it. Unaffected, and the
 * clear side of a Carrier call, rest on the member's own haplotype: a homolog of that
 * parental side seen at every position of the region. No block over the region, a block over
 * only part of it, a grey lane or an unconfirmed homolog is missing data (the member may
 * carry the risk haplotype there), so the call is uninformative. The sides a call needs are
 * those a risk haplotype was resolved on; a male is called on his one X where all his blocks
 * over the region are one-copy (outside the PARs), since a son cannot inherit his father's X.
 * In a PAR he has both copies and is called like any other member.
 *
 * X-linked recessive with the sex not recorded, the member is called both as a son and as a
 * daughter. Where the two calls differ the call assumes neither: Affected / at risk when either
 * would be (the mother's risk X: affected if male, a carrier if female), otherwise
 * uninformative. Both calls are returned, so the reason can be shown.
 */
export const assessSampleHaplotypeRisk = ({
  model,
  samples,
  member,
  region,
}: {
  model: DiseaseHaplotypeModel;
  samples: HaplotypeSampleLike[];
  member: HaplotypeMemberLike;
  region: HaplotypeRiskRegion;
}): HaplotypeRiskAssessment => {
  const call = (state: HaplotypeRiskState): HaplotypeRiskAssessment => ({
    state,
    roiNotCovered: false,
    sexDependent: null,
  });
  if (!model.informative) return call('uninformative');
  if (model.mode === 'x_linked_recessive' && normalizeSex(member.sex) === 'unknown') {
    const asSex = (sex: 'male' | 'female') =>
      assessSampleHaplotypeRisk({ model, samples, region, member: { ...member, sex } });
    const ifMale = asSex('male');
    const ifFemale = asSex('female');
    if (ifMale.state === ifFemale.state) {
      return { ...ifFemale, roiNotCovered: ifMale.roiNotCovered && ifFemale.roiNotCovered };
    }
    const atRisk = ifMale.state === 'affected_or_at_risk' || ifFemale.state === 'affected_or_at_risk';
    return {
      state: atRisk ? 'affected_or_at_risk' : 'uninformative',
      roiNotCovered: false,
      sexDependent: { ifMale: ifMale.state, ifFemale: ifFemale.state },
    };
  }
  const segmentsBySample = buildSegmentMap(samples);
  const homologs = homologsInRegion(member, segmentsBySample, region);
  const carried = new Set(homologs.map(({ signature }) => signatureKey(signature)));
  const hasKind = (kind: DiseaseHaplotypeKind): boolean =>
    model.signatures.some((signature) => signature.kind === kind && carried.has(signatureKey(signature)));
  // The call, when the member's haplotype covers the region on every one of these sides.
  const clearOn = (sides: HaplotypeOrigin[], state: HaplotypeRiskState): HaplotypeRiskAssessment =>
    sides.every((side) => coversRegionOnSide(homologs, region, side))
      ? call(state)
      : { state: 'uninformative', roiNotCovered: true, sexDependent: null };
  const blocksAtRegion = (segmentsBySample.get(member.sample_id) || []).filter((segment) =>
    segmentOverlapsRegion(segment, region),
  );
  const oneXAtRegion =
    blocksAtRegion.length > 0 && blocksAtRegion.every((segment) => isMaleWithOneX(member, segment, region.chr));
  const riskSides: HaplotypeOrigin[] = oneXAtRegion
    ? [maleXOrigin(member)]
    : Array.from(new Set(model.signatures.map((signature) => signature.origin)));

  if (model.mode === 'dominant' || model.mode === 'x_linked_dominant') {
    return hasKind(model.mode === 'dominant' ? 'dominant' : 'x-linked')
      ? call('affected_or_at_risk')
      : clearOn(riskSides, 'unaffected_non_carrier');
  }

  if (model.mode === 'recessive') {
    const hasPaternal = hasKind('recessive-paternal');
    const hasMaternal = hasKind('recessive-maternal');
    const modelHasBoth =
      model.signatures.some((signature) => signature.kind === 'recessive-paternal') &&
      model.signatures.some((signature) => signature.kind === 'recessive-maternal');
    if (!modelHasBoth) return call('uninformative');
    if (hasPaternal && hasMaternal) return call('affected_or_at_risk');
    // A carrier's other homolog must be seen clear; an unaffected member's both.
    if (hasPaternal) return clearOn(['maternal'], 'carrier');
    if (hasMaternal) return clearOn(['paternal'], 'carrier');
    return clearOn(['paternal', 'maternal'], 'unaffected_non_carrier');
  }

  if (model.mode === 'x_linked_recessive') {
    const matchingSignatures = model.signatures.filter((signature) => carried.has(signatureKey(signature)));
    if (normalizeSex(member.sex) === 'male') {
      return matchingSignatures.length > 0
        ? call('affected_or_at_risk')
        : clearOn(riskSides, 'unaffected_non_carrier');
    }
    const matchingOrigins = new Set(matchingSignatures.map((signature) => signature.origin));
    if (matchingOrigins.size >= 2) return call('affected_or_at_risk');
    // Assumed (owner to confirm): a side without a resolved risk haplotype (an unaffected father's X) is not at risk.
    const clearSides = riskSides.filter((side) => !matchingOrigins.has(side));
    return clearOn(clearSides, matchingOrigins.size === 1 ? 'carrier' : 'unaffected_non_carrier');
  }

  return call('uninformative');
};

export const interpretSampleHaplotypeRisk = (
  args: Parameters<typeof assessSampleHaplotypeRisk>[0],
): HaplotypeRiskState => assessSampleHaplotypeRisk(args).state;

export const defaultHaplotypeRiskRegion = (chrom: string, start: number, end: number): HaplotypeRiskRegion => ({
  chr: chrom,
  start,
  end: Math.max(end, start + 1),
});

export const defaultDiseaseHaplotypeModel = (inheritanceModel?: string | null): DiseaseHaplotypeModel => ({
  mode: normalizeHaplotypeInheritance(inheritanceModel),
  signatures: [],
  informative: false,
});
