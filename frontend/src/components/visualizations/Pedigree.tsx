import React, { useEffect, useMemo, useRef } from 'react';
import * as d3 from 'd3';
import { countOf } from '../../lib/countOf';
import type { PedRow } from '../../lib/pedigree';
import { isAffectedMember, isCarrierStatus } from '../../lib/familyMembers';
import { isFiniteNumber } from '../../lib/number';

interface PedigreeMember {
  sample_id: string;
  role?: string | null;
  carrier_status?: 'unknown' | 'not_carrier' | 'carrier' | boolean | null;
  carrier_type?: 'obligate' | 'proven' | 'reported' | 'inferred' | null;
  clinical_status?: string | null;
  sex?: string | null;
  affected?: boolean | null;
}

interface PedigreeRelationship {
  /** `relative`: `sample_id_b` is related through `sample_id_a`, by an unknown degree. */
  relationship_type: 'parent_child' | 'couple' | 'relative';
  sample_id_a: string;
  sample_id_b: string;
  role_a?: string | null;
  role_b?: string | null;
  metadata?: Record<string, unknown> | null;
}

export interface PedigreeQcStatus {
  status: 'pass' | 'warn' | 'fail';
  label?: string;
}

interface Props {
  rows: PedRow[];
  members?: PedigreeMember[];
  relationships?: PedigreeRelationship[];
  inheritanceModel?: string | null;
  phenotypeSampleIds?: string[];
  highlightedSampleIds?: string[];
  // Per-sample QC roll-up drawn as a ring around the node, apart from the symbol and
  // its clinical fills. The optional label becomes a tooltip.
  qcStatusBySample?: Record<string, PedigreeQcStatus>;
}

// The QC ring tells the verdicts apart by line and glyph as well as colour: a thin solid
// ring and ✓ for pass, a dashed ring and ! for warn, a thick ring and ✕ for fail (#529).
// It used to recolour the symbol itself, which replaced the black affected fill and the
// carrier-type half-fill, and told pass from warn by green versus amber alone.
const QC_MARKS: Record<
  PedigreeQcStatus['status'],
  { color: string; width: number; dash: string | null; glyph: string }
> = {
  pass: { color: '#16a34a', width: 1.5, dash: null, glyph: '✓' },
  warn: { color: '#d97706', width: 2, dash: '4 3', glyph: '!' },
  fail: { color: '#dc2626', width: 3, dash: null, glyph: '✕' },
};
const QC_RING_OFFSET = 4;

type ParentInfo = {
  father?: string;
  mother?: string;
  others: string[];
};

type FamilyUnit = {
  key: string;
  parents: string[];
  children: string[];
};

type CoupleEdge = {
  left: string;
  right: string;
  source: 'children' | 'explicit';
  metadata?: Record<string, unknown> | null;
};

/** A member related through another, by an unknown degree: drawn as a dotted arc with a "?". */
type RelativeEdge = {
  anchor: string;
  member: string;
};

type Position = {
  x: number;
  y: number;
  generation: number;
};

type LayoutBlock = {
  key: string;
  generation: number;
  members: string[];
  orderedMembers: string[];
  // Each ordered member's offset from the block's centre (the midpoint of its ends).
  offsets: number[];
  // How far the block reaches left and right of its centre, labels included.
  left: number;
  right: number;
  initialOrder: number;
};

type NormalizedPedigree = {
  rows: PedRow[];
  rowMap: Map<string, PedRow>;
  rowOrder: Map<string, number>;
  memberMap: Map<string, PedigreeMember>;
  parentInfoByChild: Map<string, ParentInfo>;
  childrenByParent: Map<string, string[]>;
  familyUnits: FamilyUnit[];
  coupleEdges: CoupleEdge[];
  relativeEdges: RelativeEdge[];
};

type LayoutResult = {
  rows: PedRow[];
  memberMap: Map<string, PedigreeMember>;
  familyUnits: FamilyUnit[];
  coupleEdges: CoupleEdge[];
  relativeEdges: RelativeEdge[];
  positions: Map<string, Position>;
  generationMembers: string[][];
  generationCount: number;
  width: number;
  height: number;
};

const NODE_SIZE = 20;
const GEN_VERTICAL_GAP = 110;
const SVG_PADDING_X = 60;
const SVG_PADDING_TOP = 50;
const SVG_PADDING_BOTTOM = 44;
const CHILD_HORIZONTAL_GAP = 44;
const COUPLE_GAP = 54;
const BLOCK_HORIZONTAL_GAP = 42;
const SIBLING_LINE_OFFSET = 24;
// Sample IDs are drawn under their symbols, an embryo's a size smaller. Neighbouring
// symbols are spaced so that their labels never touch: a long ID (FATHER_NIPT beside
// CFDNA_NIPT) used to run into the next one.
const LABEL_FONT_SIZE = 8;
const EMBRYO_LABEL_FONT_SIZE = 7;
// The least room between two neighbours' labels.
const LABEL_GAP = 6;

const labelFontSize = (member?: PedigreeMember): number =>
  member?.role === 'embryo' ? EMBRYO_LABEL_FONT_SIZE : LABEL_FONT_SIZE;

/**
 * A label's width in drawing units, estimated from its characters with generous widths
 * (capitals and digits are wide in every UI font), so the estimate is never short of what
 * a browser draws. Deterministic: the layout does not wait on fonts, and tests lay out as
 * browsers do.
 */
export const estimateLabelWidth = (text: string, fontSize: number): number => {
  let ems = 0;
  for (const char of text) {
    if ('MW'.includes(char)) ems += 1;
    else if ('mw'.includes(char)) ems += 0.92;
    else if ("Iijl.,:;!'".includes(char)) ems += 0.34;
    else if ('CDGHNOQU'.includes(char)) ems += 0.82;
    else if (/[A-Z]/.test(char)) ems += 0.74;
    else if (/[0-9]/.test(char)) ems += 0.66;
    else ems += 0.62;
  }
  // Whole units, rounded up: the positions stay round numbers.
  return Math.ceil(ems * fontSize);
};

// Half of each member's label: how far it reaches either side of the symbol's centre.
type LabelHalves = Map<string, number>;

// Spacing set by labels is rounded up to whole, even units, as the fixed gaps are: a
// couple's midpoint, where the line to its children drops, then stays on a whole unit
// and the line is drawn crisp instead of blurred across two pixels.
const evenUnits = (value: number): number => 2 * Math.ceil(value / 2);

/** Two partners' centre distance: the couple gap, or more when their labels would touch. */
const partnerGap = (left: string, right: string, halves: LabelHalves): number =>
  Math.max(
    COUPLE_GAP,
    evenUnits((halves.get(left) ?? 0) + (halves.get(right) ?? 0) + LABEL_GAP)
  );

/**
 * How far a block's end member reaches outward when blocks are spaced apart: its symbol,
 * or its label less the part that the gap between blocks already keeps clear. Short labels
 * keep the symbols' spacing; a long one pushes the next block away until the labels clear.
 */
const blockEndReach = (member: string, halves: LabelHalves): number =>
  Math.max(
    NODE_SIZE / 2,
    Math.ceil((halves.get(member) ?? 0) + (LABEL_GAP - BLOCK_HORIZONTAL_GAP) / 2)
  );

const isAffectedPhenotype = (phenotype: string): boolean => phenotype === '2';

const trimId = (sampleId?: string | null): string => (sampleId || '').trim();

const average = (values: number[]): number | undefined => {
  if (!values.length) return undefined;
  return values.reduce((sum, value) => sum + value, 0) / values.length;
};

const sexCodeFromMember = (member?: PedigreeMember): string => {
  if (member?.sex === 'male' || member?.sex === '1') return '1';
  if (member?.sex === 'female' || member?.sex === '2') return '2';
  return '0';
};

const phenotypeFromMember = (member?: PedigreeMember): string => {
  if (member?.clinical_status === 'affected' || member?.affected) return '2';
  if (member?.clinical_status === 'unaffected') return '1';
  return '0';
};

const sexHintFromParentRole = (role?: string | null): string | undefined => {
  const normalizedRole = (role || '').trim().toLowerCase();
  if (normalizedRole === 'father') return '1';
  if (normalizedRole === 'mother') return '2';
  return undefined;
};

const familyKey = (parents: string[]): string => [...parents].sort().join('|');

const pairKey = (left: string, right: string): string =>
  [left, right].sort().join('|');

const addUnique = (values: string[], value?: string | null) => {
  const cleaned = trimId(value);
  if (cleaned && cleaned !== '0' && !values.includes(cleaned)) {
    values.push(cleaned);
  }
};

const carrierFillFor = (
  carrierType?: PedigreeMember['carrier_type']
): string => {
  if (carrierType === 'obligate') return '#2563eb';
  if (carrierType === 'reported') return '#7c3aed';
  if (carrierType === 'inferred') return '#0f766e';
  return 'black';
};

const normalizedSexFor = (row: PedRow, member?: PedigreeMember): string => {
  if (row.sex && row.sex !== '0') return row.sex;
  return sexCodeFromMember(member);
};

const sampleOrder = (sampleId: string, rowOrder: Map<string, number>): number =>
  rowOrder.get(sampleId) ?? Number.MAX_SAFE_INTEGER;

const normalizePedigree = (
  rows: PedRow[],
  members: PedigreeMember[],
  relationships: PedigreeRelationship[]
): NormalizedPedigree => {
  const memberMap = new Map(
    members.map((member) => [member.sample_id, member])
  );
  const normalizedRows: PedRow[] = [];
  const rowMap = new Map<string, PedRow>();
  const rowOrder = new Map<string, number>();
  const fallbackFamilyId = rows[0]?.fid || 'FAM';

  const addRow = (sampleId: string, sexHint?: string): PedRow | undefined => {
    const iid = trimId(sampleId);
    if (!iid || iid === '0') return undefined;
    const existing = rowMap.get(iid);
    if (existing) {
      if ((!existing.sex || existing.sex === '0') && sexHint) {
        existing.sex = sexHint;
      }
      return existing;
    }

    const member = memberMap.get(iid);
    const row: PedRow = {
      fid: fallbackFamilyId,
      iid,
      pid: '0',
      mid: '0',
      sex: sexHint || sexCodeFromMember(member),
      phen: phenotypeFromMember(member),
    };
    rowMap.set(iid, row);
    rowOrder.set(iid, rowOrder.size);
    normalizedRows.push(row);
    return row;
  };

  rows.forEach((row) => {
    const iid = trimId(row.iid);
    if (!iid || rowMap.has(iid)) return;
    const member = memberMap.get(iid);
    const normalizedRow: PedRow = {
      fid: row.fid || fallbackFamilyId,
      iid,
      pid: trimId(row.pid) || '0',
      mid: trimId(row.mid) || '0',
      sex: row.sex && row.sex !== '0' ? row.sex : sexCodeFromMember(member),
      phen: row.phen || phenotypeFromMember(member),
    };
    rowMap.set(iid, normalizedRow);
    rowOrder.set(iid, rowOrder.size);
    normalizedRows.push(normalizedRow);
  });

  members.forEach((member) => addRow(member.sample_id));

  [...normalizedRows].forEach((row) => {
    addRow(row.pid, '1');
    addRow(row.mid, '2');
  });

  relationships.forEach((relationship) => {
    if (relationship.relationship_type === 'parent_child') {
      addRow(
        relationship.sample_id_a,
        sexHintFromParentRole(relationship.role_a)
      );
      addRow(relationship.sample_id_b);
    } else if (
      relationship.relationship_type === 'couple' ||
      relationship.relationship_type === 'relative'
    ) {
      addRow(relationship.sample_id_a);
      addRow(relationship.sample_id_b);
    }
  });

  const parentInfoByChild = new Map<string, ParentInfo>();
  const ensureParentInfo = (childId: string): ParentInfo => {
    const existing = parentInfoByChild.get(childId);
    if (existing) return existing;
    const created: ParentInfo = { others: [] };
    parentInfoByChild.set(childId, created);
    return created;
  };

  normalizedRows.forEach((row) => {
    const info = ensureParentInfo(row.iid);
    if (row.pid && row.pid !== '0') info.father = row.pid;
    if (row.mid && row.mid !== '0') info.mother = row.mid;
  });

  relationships.forEach((relationship) => {
    if (relationship.relationship_type !== 'parent_child') return;
    const parentId = trimId(relationship.sample_id_a);
    const childId = trimId(relationship.sample_id_b);
    if (!parentId || !childId || parentId === childId) return;
    const info = ensureParentInfo(childId);
    const role = (relationship.role_a || '').toLowerCase();
    if (role === 'father') {
      info.father = parentId;
    } else if (role === 'mother') {
      info.mother = parentId;
    } else {
      addUnique(info.others, parentId);
    }
  });

  const parentIdsFor = (childId: string): string[] => {
    const info = parentInfoByChild.get(childId);
    if (!info) return [];
    const parents: string[] = [];
    addUnique(parents, info.father);
    addUnique(parents, info.mother);
    info.others.forEach((parentId) => addUnique(parents, parentId));
    return parents;
  };

  const childrenByParent = new Map<string, string[]>();
  parentInfoByChild.forEach((_info, childId) => {
    parentIdsFor(childId).forEach((parentId) => {
      const children = childrenByParent.get(parentId) || [];
      addUnique(children, childId);
      childrenByParent.set(parentId, children);
    });
  });

  const familyUnitsByKey = new Map<string, FamilyUnit>();
  parentInfoByChild.forEach((_info, childId) => {
    const parents = parentIdsFor(childId);
    if (!parents.length) return;
    const key = familyKey(parents);
    if (!familyUnitsByKey.has(key)) {
      familyUnitsByKey.set(key, { key, parents, children: [] });
    }
    addUnique(familyUnitsByKey.get(key)!.children, childId);
  });

  const coupleEdgesByKey = new Map<string, CoupleEdge>();
  const addCoupleEdge = (
    left: string,
    right: string,
    source: CoupleEdge['source'],
    metadata?: Record<string, unknown> | null
  ) => {
    const leftId = trimId(left);
    const rightId = trimId(right);
    if (!leftId || !rightId || leftId === rightId) return;
    const key = pairKey(leftId, rightId);
    const existing = coupleEdgesByKey.get(key);
    if (
      !existing ||
      (existing.source === 'children' && source === 'explicit')
    ) {
      coupleEdgesByKey.set(key, {
        left: leftId,
        right: rightId,
        source,
        metadata,
      });
    }
  };

  familyUnitsByKey.forEach((unit) => {
    for (let i = 0; i < unit.parents.length; i += 1) {
      for (let j = i + 1; j < unit.parents.length; j += 1) {
        addCoupleEdge(unit.parents[i], unit.parents[j], 'children');
      }
    }
    unit.children.sort(
      (left, right) =>
        sampleOrder(left, rowOrder) - sampleOrder(right, rowOrder)
    );
  });

  relationships.forEach((relationship) => {
    if (relationship.relationship_type !== 'couple') return;
    const parents: string[] = [];
    addUnique(parents, relationship.sample_id_a);
    addUnique(parents, relationship.sample_id_b);
    if (parents.length !== 2) return;
    const key = familyKey(parents);
    if (!familyUnitsByKey.has(key)) {
      familyUnitsByKey.set(key, { key, parents, children: [] });
    }
    addCoupleEdge(parents[0], parents[1], 'explicit', relationship.metadata);
  });

  const relativeEdgesByKey = new Map<string, RelativeEdge>();
  relationships.forEach((relationship) => {
    if (relationship.relationship_type !== 'relative') return;
    const anchor = trimId(relationship.sample_id_a);
    const member = trimId(relationship.sample_id_b);
    if (!anchor || !member || anchor === member) return;
    relativeEdgesByKey.set(`${anchor}|${member}`, { anchor, member });
  });

  const familyUnits = [...familyUnitsByKey.values()].sort((left, right) => {
    const leftOrder = Math.min(
      ...left.parents.map((parentId) => sampleOrder(parentId, rowOrder))
    );
    const rightOrder = Math.min(
      ...right.parents.map((parentId) => sampleOrder(parentId, rowOrder))
    );
    if (leftOrder !== rightOrder) return leftOrder - rightOrder;
    return left.key.localeCompare(right.key);
  });

  return {
    rows: normalizedRows,
    rowMap,
    rowOrder,
    memberMap,
    parentInfoByChild,
    childrenByParent,
    familyUnits,
    coupleEdges: [...coupleEdgesByKey.values()],
    relativeEdges: [...relativeEdgesByKey.values()],
  };
};

const parentIdsFromInfo = (info?: ParentInfo): string[] => {
  if (!info) return [];
  const parents: string[] = [];
  addUnique(parents, info.father);
  addUnique(parents, info.mother);
  info.others.forEach((parentId) => addUnique(parents, parentId));
  return parents;
};

const assignGenerations = (
  normalized: NormalizedPedigree
): Map<string, number> => {
  const generationCache = new Map<string, number>();
  const visiting = new Set<string>();

  const getInitialGeneration = (sampleId: string): number => {
    if (generationCache.has(sampleId)) return generationCache.get(sampleId)!;
    if (visiting.has(sampleId)) return 0;
    visiting.add(sampleId);
    const parents = parentIdsFromInfo(
      normalized.parentInfoByChild.get(sampleId)
    );
    const generation = parents.length
      ? Math.max(
          ...parents.map((parentId) => getInitialGeneration(parentId) + 1)
        )
      : 0;
    visiting.delete(sampleId);
    generationCache.set(sampleId, generation);
    return generation;
  };

  normalized.rows.forEach((row) => getInitialGeneration(row.iid));

  const isAncestor = (ancestorId: string, targetId: string): boolean => {
    const stack = [...(normalized.childrenByParent.get(ancestorId) || [])];
    const seen = new Set<string>();
    while (stack.length) {
      const current = stack.pop()!;
      if (current === targetId) return true;
      if (seen.has(current)) continue;
      seen.add(current);
      stack.push(...(normalized.childrenByParent.get(current) || []));
    }
    return false;
  };

  const maxIterations = normalized.rows.length * 4 + 4;
  for (let iteration = 0; iteration < maxIterations; iteration += 1) {
    let changed = false;

    normalized.coupleEdges.forEach((edge) => {
      if (
        isAncestor(edge.left, edge.right) ||
        isAncestor(edge.right, edge.left)
      )
        return;
      const leftGeneration = generationCache.get(edge.left) ?? 0;
      const rightGeneration = generationCache.get(edge.right) ?? 0;
      const alignedGeneration = Math.max(leftGeneration, rightGeneration);
      if (leftGeneration !== alignedGeneration) {
        generationCache.set(edge.left, alignedGeneration);
        changed = true;
      }
      if (rightGeneration !== alignedGeneration) {
        generationCache.set(edge.right, alignedGeneration);
        changed = true;
      }
    });

    // A member related to the family by an unknown degree, without recorded parents,
    // stands in the generation of the member it is related through.
    normalized.relativeEdges.forEach((edge) => {
      if (parentIdsFromInfo(normalized.parentInfoByChild.get(edge.member)).length) return;
      const anchorGeneration = generationCache.get(edge.anchor) ?? 0;
      if ((generationCache.get(edge.member) ?? 0) < anchorGeneration) {
        generationCache.set(edge.member, anchorGeneration);
        changed = true;
      }
    });

    normalized.parentInfoByChild.forEach((_info, childId) => {
      const parents = parentIdsFromInfo(
        normalized.parentInfoByChild.get(childId)
      );
      if (!parents.length) return;
      const childGeneration = generationCache.get(childId) ?? 0;
      const requiredGeneration =
        Math.max(
          ...parents.map((parentId) => generationCache.get(parentId) ?? 0)
        ) + 1;
      if (childGeneration < requiredGeneration) {
        generationCache.set(childId, requiredGeneration);
        changed = true;
      }
    });

    if (!changed) break;
  }

  normalized.rows.forEach((row) => {
    if (!generationCache.has(row.iid)) {
      generationCache.set(row.iid, 0);
    }
  });

  return generationCache;
};

class DisjointSet {
  private parents = new Map<string, string>();

  constructor(values: string[]) {
    values.forEach((value) => this.parents.set(value, value));
  }

  find(value: string): string {
    const parent = this.parents.get(value);
    if (!parent || parent === value) return value;
    const root = this.find(parent);
    this.parents.set(value, root);
    return root;
  }

  union(left: string, right: string) {
    const leftRoot = this.find(left);
    const rightRoot = this.find(right);
    if (leftRoot !== rightRoot) {
      this.parents.set(rightRoot, leftRoot);
    }
  }
}

const rowSexRank = (row?: PedRow): number => {
  if (row?.sex === '1') return 0;
  if (row?.sex === '2') return 1;
  return 2;
};

const orderBlockMembers = (
  members: string[],
  coupleEdges: CoupleEdge[],
  generations: Map<string, number>,
  rowMap: Map<string, PedRow>,
  rowOrder: Map<string, number>
): string[] => {
  const sortedMembers = [...members].sort(
    (left, right) => sampleOrder(left, rowOrder) - sampleOrder(right, rowOrder)
  );
  if (sortedMembers.length <= 1) return sortedMembers;

  const memberSet = new Set(sortedMembers);
  const adjacency = new Map<string, string[]>();
  sortedMembers.forEach((member) => adjacency.set(member, []));
  coupleEdges.forEach((edge) => {
    if (
      memberSet.has(edge.left) &&
      memberSet.has(edge.right) &&
      generations.get(edge.left) === generations.get(edge.right)
    ) {
      adjacency.get(edge.left)?.push(edge.right);
      adjacency.get(edge.right)?.push(edge.left);
    }
  });

  adjacency.forEach((partners) => {
    partners.sort(
      (left, right) =>
        sampleOrder(left, rowOrder) - sampleOrder(right, rowOrder)
    );
  });

  if (sortedMembers.length === 2) {
    const [left, right] = sortedMembers;
    const arePartners = adjacency.get(left)?.includes(right);
    if (!arePartners) return sortedMembers;
    return [left, right].sort((a, b) => {
      const sexDelta = rowSexRank(rowMap.get(a)) - rowSexRank(rowMap.get(b));
      if (sexDelta !== 0) return sexDelta;
      return sampleOrder(a, rowOrder) - sampleOrder(b, rowOrder);
    });
  }

  const degrees = sortedMembers.map((member) => ({
    member,
    degree: adjacency.get(member)?.length || 0,
  }));
  const isPathLike = degrees.every(({ degree }) => degree <= 2);
  if (isPathLike) {
    const endpoint = [...degrees]
      .filter(({ degree }) => degree <= 1)
      .sort(
        (left, right) =>
          sampleOrder(left.member, rowOrder) -
          sampleOrder(right.member, rowOrder)
      )[0];
    const ordered: string[] = [];
    let current = endpoint?.member || sortedMembers[0];
    let previous: string | undefined;
    while (current && !ordered.includes(current)) {
      ordered.push(current);
      const next = (adjacency.get(current) || []).find(
        (partner) => partner !== previous
      );
      previous = current;
      current = next || '';
    }
    if (ordered.length === sortedMembers.length) return ordered;
  }

  const center = [...degrees].sort((left, right) => {
    if (right.degree !== left.degree) return right.degree - left.degree;
    return (
      sampleOrder(left.member, rowOrder) - sampleOrder(right.member, rowOrder)
    );
  })[0].member;
  const around = sortedMembers.filter((member) => member !== center);
  const splitIndex = Math.ceil(around.length / 2);
  return [...around.slice(0, splitIndex), center, ...around.slice(splitIndex)];
};

/**
 * Where a member related by an unknown degree goes when it stands alone in its block:
 * beside the member it is related through, left of a father (or a male anchor) and
 * right of a mother or of both parents, so its dotted arc stays short and the couple
 * keeps its place over their children.
 */
const relatedBlockOrder = (
  members: string[],
  normalized: NormalizedPedigree,
  generations: Map<string, number>
): number | undefined => {
  if (members.length !== 1) return undefined;
  const [member] = members;
  const anchors = normalized.relativeEdges
    .filter(
      (edge) =>
        edge.member === member &&
        generations.get(edge.anchor) === generations.get(member)
    )
    .map((edge) => edge.anchor);
  if (!anchors.length) return undefined;
  const orders = anchors.map((anchor) => sampleOrder(anchor, normalized.rowOrder));
  const leftOfAnchor =
    anchors.length === 1 && rowSexRank(normalized.rowMap.get(anchors[0])) === 0;
  return leftOfAnchor ? Math.min(...orders) - 0.5 : Math.max(...orders) + 0.5;
};

const buildBlocksByGeneration = (
  normalized: NormalizedPedigree,
  generations: Map<string, number>,
  labelHalves: LabelHalves
): Map<number, LayoutBlock[]> => {
  const idsByGeneration = new Map<number, string[]>();
  normalized.rows.forEach((row) => {
    const generation = generations.get(row.iid) ?? 0;
    const ids = idsByGeneration.get(generation) || [];
    ids.push(row.iid);
    idsByGeneration.set(generation, ids);
  });

  const blocksByGeneration = new Map<number, LayoutBlock[]>();
  idsByGeneration.forEach((ids, generation) => {
    ids.sort(
      (left, right) =>
        sampleOrder(left, normalized.rowOrder) -
        sampleOrder(right, normalized.rowOrder)
    );
    const dsu = new DisjointSet(ids);
    normalized.coupleEdges.forEach((edge) => {
      if (
        ids.includes(edge.left) &&
        ids.includes(edge.right) &&
        generations.get(edge.left) === generation &&
        generations.get(edge.right) === generation
      ) {
        dsu.union(edge.left, edge.right);
      }
    });

    const membersByRoot = new Map<string, string[]>();
    ids.forEach((id) => {
      const root = dsu.find(id);
      const members = membersByRoot.get(root) || [];
      members.push(id);
      membersByRoot.set(root, members);
    });

    const blocks = [...membersByRoot.values()].map((members) => {
      const orderedMembers = orderBlockMembers(
        members,
        normalized.coupleEdges,
        generations,
        normalized.rowMap,
        normalized.rowOrder
      );
      const key = `${generation}:${[...members].sort().join('|')}`;
      const gaps = orderedMembers
        .slice(1)
        .map((member, index) => partnerGap(orderedMembers[index], member, labelHalves));
      const span = gaps.reduce((sum, gap) => sum + gap, 0);
      const offsets: number[] = [];
      let offset = -span / 2;
      orderedMembers.forEach((_, index) => {
        if (index > 0) offset += gaps[index - 1];
        offsets.push(offset);
      });
      return {
        key,
        generation,
        members,
        orderedMembers,
        offsets,
        left: span / 2 + blockEndReach(orderedMembers[0], labelHalves),
        right: span / 2 + blockEndReach(orderedMembers[orderedMembers.length - 1], labelHalves),
        initialOrder:
          relatedBlockOrder(members, normalized, generations) ??
          Math.min(
            ...members.map((member) => sampleOrder(member, normalized.rowOrder))
          ),
      };
    });

    blocks.sort((left, right) => {
      if (left.initialOrder !== right.initialOrder)
        return left.initialOrder - right.initialOrder;
      return left.key.localeCompare(right.key);
    });
    blocksByGeneration.set(generation, blocks);
  });

  return blocksByGeneration;
};

const blockOrderMapsFor = (
  blocksByGeneration: Map<number, LayoutBlock[]>
): Map<number, Map<string, number>> => {
  const orderMaps = new Map<number, Map<string, number>>();
  blocksByGeneration.forEach((blocks, generation) => {
    orderMaps.set(
      generation,
      new Map(blocks.map((block, index) => [block.key, index]))
    );
  });
  return orderMaps;
};

const refineBlockOrdering = (
  normalized: NormalizedPedigree,
  generations: Map<string, number>,
  blocksByGeneration: Map<number, LayoutBlock[]>,
  memberToBlock: Map<string, LayoutBlock>
) => {
  const generationCount =
    Math.max(
      0,
      ...normalized.rows.map((row) => generations.get(row.iid) ?? 0)
    ) + 1;

  const scoreBlocks = (
    blocks: LayoutBlock[],
    scoreFor: (
      block: LayoutBlock,
      orderMaps: Map<number, Map<string, number>>
    ) => number | undefined
  ): LayoutBlock[] => {
    const orderMaps = blockOrderMapsFor(blocksByGeneration);
    return blocks
      .map((block, index) => ({
        block,
        index,
        score: scoreFor(block, orderMaps),
      }))
      .sort((left, right) => {
        if (isFiniteNumber(left.score) && isFiniteNumber(right.score)) {
          const delta = left.score - right.score;
          if (Math.abs(delta) > 0.001) return delta;
        }
        return left.index - right.index;
      })
      .map(({ block }) => block);
  };

  const parentScore = (
    block: LayoutBlock,
    orderMaps: Map<number, Map<string, number>>
  ): number | undefined => {
    const scores: number[] = [];
    block.members.forEach((member) => {
      parentIdsFromInfo(normalized.parentInfoByChild.get(member)).forEach(
        (parentId) => {
          const parentBlock = memberToBlock.get(parentId);
          if (!parentBlock || parentBlock.generation === block.generation)
            return;
          const score = orderMaps
            .get(parentBlock.generation)
            ?.get(parentBlock.key);
          if (score !== undefined) scores.push(score);
        }
      );
    });
    return average(scores);
  };

  const childScore = (
    block: LayoutBlock,
    orderMaps: Map<number, Map<string, number>>
  ): number | undefined => {
    const scores: number[] = [];
    block.members.forEach((member) => {
      (normalized.childrenByParent.get(member) || []).forEach((childId) => {
        const childBlock = memberToBlock.get(childId);
        if (!childBlock || childBlock.generation === block.generation) return;
        const score = orderMaps.get(childBlock.generation)?.get(childBlock.key);
        if (score !== undefined) scores.push(score);
      });
    });
    return average(scores);
  };

  for (let iteration = 0; iteration < 6; iteration += 1) {
    for (let generation = 1; generation < generationCount; generation += 1) {
      const blocks = blocksByGeneration.get(generation);
      if (blocks) {
        blocksByGeneration.set(generation, scoreBlocks(blocks, parentScore));
      }
    }
    for (
      let generation = generationCount - 2;
      generation >= 0;
      generation -= 1
    ) {
      const blocks = blocksByGeneration.get(generation);
      if (blocks) {
        blocksByGeneration.set(generation, scoreBlocks(blocks, childScore));
      }
    }
  }
};

const layoutPedigree = (
  rows: PedRow[],
  members: PedigreeMember[],
  relationships: PedigreeRelationship[]
): LayoutResult => {
  const normalized = normalizePedigree(rows, members, relationships);
  if (!normalized.rows.length) {
    return {
      rows: [],
      memberMap: normalized.memberMap,
      familyUnits: [],
      coupleEdges: [],
      relativeEdges: [],
      positions: new Map(),
      generationMembers: [],
      generationCount: 0,
      width: 0,
      height: 0,
    };
  }

  const generations = assignGenerations(normalized);
  const generationCount =
    Math.max(
      0,
      ...normalized.rows.map((row) => generations.get(row.iid) ?? 0)
    ) + 1;
  const labelHalves: LabelHalves = new Map(
    normalized.rows.map((row) => [
      row.iid,
      estimateLabelWidth(row.iid, labelFontSize(normalized.memberMap.get(row.iid))) / 2,
    ])
  );
  const blocksByGeneration = buildBlocksByGeneration(normalized, generations, labelHalves);
  const memberToBlock = new Map<string, LayoutBlock>();
  blocksByGeneration.forEach((blocks) => {
    blocks.forEach((block) => {
      block.members.forEach((member) => memberToBlock.set(member, block));
    });
  });
  refineBlockOrdering(
    normalized,
    generations,
    blocksByGeneration,
    memberToBlock
  );

  const positions = new Map<string, Position>();
  const addDesiredCenter = (
    desiredCenters: Map<string, number[]>,
    block: LayoutBlock | undefined,
    desiredCenter: number
  ) => {
    if (!block || !Number.isFinite(desiredCenter)) return;
    const centers = desiredCenters.get(block.key) || [];
    centers.push(desiredCenter);
    desiredCenters.set(block.key, centers);
  };

  for (let generation = 0; generation < generationCount; generation += 1) {
    const blocks = blocksByGeneration.get(generation) || [];
    const desiredCenters = new Map<string, number[]>();
    const blockOrder = new Map(
      blocks.map((block, index) => [block.key, index])
    );

    normalized.familyUnits.forEach((unit) => {
      const childBlocks = Array.from(
        new Set(
          unit.children
            .filter((childId) => generations.get(childId) === generation)
            .map((childId) => memberToBlock.get(childId))
            .filter((block): block is LayoutBlock => Boolean(block))
        )
      ).sort(
        (left, right) =>
          (blockOrder.get(left.key) ?? 0) - (blockOrder.get(right.key) ?? 0)
      );
      if (!childBlocks.length) return;
      const parentPositions = unit.parents
        .map((parentId) => positions.get(parentId))
        .filter((position): position is Position => Boolean(position));
      const parentCenter = average(
        parentPositions.map((position) => position.x)
      );
      if (!isFiniteNumber(parentCenter)) return;
      childBlocks.forEach((block, index) => {
        const offset =
          (index - (childBlocks.length - 1) / 2) * CHILD_HORIZONTAL_GAP;
        addDesiredCenter(desiredCenters, block, parentCenter + offset);
      });
    });

    let cursor = 0;
    blocks.forEach((block) => {
      const desiredCenter = average(desiredCenters.get(block.key) || []);
      const minimumCenter = cursor + block.left;
      const center = Math.max(desiredCenter ?? minimumCenter, minimumCenter);
      block.orderedMembers.forEach((member, index) => {
        positions.set(member, {
          x: center + block.offsets[index],
          y: generation * GEN_VERTICAL_GAP + SVG_PADDING_TOP,
          generation,
        });
      });
      cursor = center + block.right + BLOCK_HORIZONTAL_GAP;
    });
  }

  // The midpoint of the block's ends, which its reach is measured from (with partners
  // spaced unevenly by their labels, the mean of its members is not).
  const positionedBlockCenter = (block: LayoutBlock): number | undefined => {
    const xs = block.orderedMembers
      .map((member) => positions.get(member)?.x)
      .filter(isFiniteNumber);
    return xs.length ? (Math.min(...xs) + Math.max(...xs)) / 2 : undefined;
  };

  const shiftBlock = (block: LayoutBlock, delta: number) => {
    if (!Number.isFinite(delta) || Math.abs(delta) < 0.001) return;
    block.orderedMembers.forEach((member) => {
      const position = positions.get(member);
      if (position) {
        position.x += delta;
      }
    });
  };

  for (
    let generation = generationCount - 2;
    generation >= 0;
    generation -= 1
  ) {
    const blocks = blocksByGeneration.get(generation) || [];
    if (!blocks.length) continue;
    const desiredCenters = new Map<string, number[]>();

    normalized.familyUnits.forEach((unit) => {
      const childPositions = unit.children
        .map((childId) => positions.get(childId))
        .filter((position): position is Position => Boolean(position));
      if (!childPositions.length) return;
      const childXs = childPositions.map((position) => position.x);
      const childCenter = (Math.min(...childXs) + Math.max(...childXs)) / 2;
      const parentBlocks = new Map<string, LayoutBlock>();
      unit.parents.forEach((parentId) => {
        const parentPosition = positions.get(parentId);
        const parentBlock = memberToBlock.get(parentId);
        if (parentPosition?.generation === generation && parentBlock) {
          parentBlocks.set(parentBlock.key, parentBlock);
        }
      });
      parentBlocks.forEach((block) => {
        addDesiredCenter(desiredCenters, block, childCenter);
      });
    });

    if (!desiredCenters.size) continue;
    const positionedBlocks = blocks
      .map((block) => ({
        block,
        currentCenter: positionedBlockCenter(block),
      }))
      .filter(
        (entry): entry is { block: LayoutBlock; currentCenter: number } =>
          isFiniteNumber(entry.currentCenter)
      )
      .sort((left, right) => left.currentCenter - right.currentCenter);

    let previousRight: number | undefined;
    positionedBlocks.forEach(({ block, currentCenter }) => {
      const targetCenter =
        average(desiredCenters.get(block.key) || []) ?? currentCenter;
      const minimumCenter =
        previousRight === undefined
          ? targetCenter
          : previousRight + BLOCK_HORIZONTAL_GAP + block.left;
      const nextCenter = Math.max(targetCenter, minimumCenter);
      shiftBlock(block, nextCenter - currentCenter);
      previousRight = nextCenter + block.right;
    });
  }

  let minX = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  positions.forEach((position, sampleId) => {
    // A label longer than the side padding leaves room for would be cut off at the edge.
    const reach = Math.max(
      NODE_SIZE / 2,
      Math.ceil((labelHalves.get(sampleId) ?? 0) - SVG_PADDING_X + LABEL_GAP)
    );
    minX = Math.min(minX, position.x - reach);
    maxX = Math.max(maxX, position.x + reach);
    maxY = Math.max(maxY, position.y);
  });
  if (!Number.isFinite(minX)) {
    minX = 0;
    maxX = 0;
    maxY = 0;
  }
  const offsetX = SVG_PADDING_X - minX;
  positions.forEach((position) => {
    position.x += offsetX;
  });

  const generationMembers = Array.from(
    { length: generationCount },
    (_, generation) =>
      [...positions.entries()]
        .filter(([, position]) => position.generation === generation)
        .sort((left, right) => left[1].x - right[1].x)
        .map(([sampleId]) => sampleId)
  );

  return {
    rows: normalized.rows,
    memberMap: normalized.memberMap,
    familyUnits: normalized.familyUnits,
    coupleEdges: normalized.coupleEdges,
    relativeEdges: normalized.relativeEdges,
    positions,
    generationMembers,
    generationCount,
    width: maxX - minX + SVG_PADDING_X * 2,
    height: maxY + SVG_PADDING_BOTTOM,
  };
};

const isConsanguineous = (
  metadata?: Record<string, unknown> | null
): boolean => {
  if (!metadata) return false;
  if (metadata.consanguineous === true) return true;
  const context = String(
    metadata.context || metadata.relationship || ''
  ).toLowerCase();
  return context.includes('consanguin') || context.includes('related');
};

const isAffectedRow = (row: PedRow, member?: PedigreeMember): boolean =>
  isAffectedPhenotype(row.phen) || (member !== undefined && isAffectedMember(member));

/**
 * The pedigree's accessible name. `role="img"` hides the per-symbol tooltips from
 * assistive technology, so the name carries what the symbols draw: who is affected, the
 * carriers (the half-fill, which the drawing gives only to an unaffected carrier), the
 * consanguineous couples (the double line), the HPO badges and the QC rings.
 */
const describePedigree = (
  layout: LayoutResult,
  phenotypeSampleIds: string[],
  qcStatusBySample: Record<string, PedigreeQcStatus>
): string => {
  if (!layout.rows.length) return 'Pedigree: no members';
  const phenotypeSampleSet = new Set(phenotypeSampleIds);
  let affected = 0;
  let carriers = 0;
  let withPhenotypes = 0;
  const qcCounts: Record<PedigreeQcStatus['status'], number> = { fail: 0, warn: 0, pass: 0 };
  layout.rows.forEach((row) => {
    const member = layout.memberMap.get(row.iid);
    const isAffected = isAffectedRow(row, member);
    if (isAffected) affected += 1;
    else if (isCarrierStatus(member?.carrier_status)) carriers += 1;
    if (phenotypeSampleSet.has(row.iid)) withPhenotypes += 1;
    const qc = qcStatusBySample[row.iid];
    if (qc) qcCounts[qc.status] += 1;
  });
  const consanguineousCouples = layout.coupleEdges.filter((edge) => {
    const left = layout.positions.get(edge.left);
    const right = layout.positions.get(edge.right);
    return (
      !!left && !!right && left.generation === right.generation && isConsanguineous(edge.metadata)
    );
  }).length;

  const relatedMembers = new Set(layout.relativeEdges.map((edge) => edge.member)).size;
  const facts = [
    `${countOf(layout.rows.length, 'member')} in ${countOf(layout.generationCount, 'generation')}`,
    `${affected.toLocaleString()} affected`,
    carriers ? countOf(carriers, 'carrier') : null,
    consanguineousCouples ? countOf(consanguineousCouples, 'consanguineous couple') : null,
    relatedMembers
      ? `${countOf(relatedMembers, 'member')} related by an unknown degree (dotted)`
      : null,
    withPhenotypes ? `${withPhenotypes.toLocaleString()} with HPO phenotypes` : null,
  ].filter(Boolean);
  const qc = (['fail', 'warn', 'pass'] as const)
    .filter((status) => qcCounts[status] > 0)
    .map((status) => `${qcCounts[status].toLocaleString()} ${status}`);
  return `Pedigree: ${facts.join(', ')}${qc.length ? `; QC: ${qc.join(', ')}` : ''}`;
};

const Pedigree: React.FC<Props> = ({
  rows,
  members = [],
  relationships = [],
  inheritanceModel,
  phenotypeSampleIds = [],
  highlightedSampleIds = [],
  qcStatusBySample = {},
}) => {
  const svgRef = useRef<SVGSVGElement | null>(null);
  // Laid out once for both the drawing and the accessible name.
  const layout = useMemo(
    () => layoutPedigree(rows, members, relationships),
    [rows, members, relationships]
  );
  const chartLabel = useMemo(
    () => describePedigree(layout, phenotypeSampleIds, qcStatusBySample),
    [layout, phenotypeSampleIds, qcStatusBySample]
  );

  useEffect(() => {
    const normalizedInheritance = (inheritanceModel || '').trim().toUpperCase();
    const phenotypeSampleSet = new Set(phenotypeSampleIds);
    const highlightedSampleSet = new Set(highlightedSampleIds);
    const svg = d3.select(svgRef.current);
    svg.selectAll('*').remove();
    svg
      .attr('width', layout.width)
      .attr('height', layout.height)
      .attr('data-generation-count', layout.generationCount)
      .attr(
        'data-generation-members',
        JSON.stringify(layout.generationMembers)
      );

    const appendLine = (
      x1: number,
      y1: number,
      x2: number,
      y2: number,
      options: { dashed?: boolean } = {}
    ) => {
      const line = svg
        .append('line')
        .attr('x1', x1)
        .attr('y1', y1)
        .attr('x2', x2)
        .attr('y2', y2)
        .attr('stroke', 'black')
        .attr('stroke-linecap', 'round');
      if (options.dashed) {
        line.attr('stroke-dasharray', '4 4');
      }
      return line;
    };

    const appendPolyline = (
      points: Array<[number, number]>,
      options: { dashed?: boolean } = {}
    ) => {
      const polyline = svg
        .append('polyline')
        .attr('points', points.map(([x, y]) => `${x},${y}`).join(' '))
        .attr('fill', 'none')
        .attr('stroke', 'black')
        .attr('stroke-linecap', 'round')
        .attr('stroke-linejoin', 'round');
      if (options.dashed) {
        polyline.attr('stroke-dasharray', '4 4');
      }
      return polyline;
    };

    layout.coupleEdges.forEach((edge) => {
      const left = layout.positions.get(edge.left);
      const right = layout.positions.get(edge.right);
      if (!left || !right) return;
      const relatedPartners = isConsanguineous(edge.metadata);
      if (left.generation === right.generation) {
        const yOffsets = relatedPartners ? [-3, 3] : [0];
        yOffsets.forEach((offset) => {
          appendLine(left.x, left.y + offset, right.x, right.y + offset);
        });
      } else {
        const midY = (left.y + right.y) / 2;
        appendPolyline(
          [
            [left.x, left.y],
            [left.x, midY],
            [right.x, midY],
            [right.x, right.y],
          ],
          { dashed: true }
        );
      }
    });

    // A member related by an unknown degree: a dotted arc to the member it is related
    // through, with a "?" for the degree, apart from the solid couple line and the
    // dashed line of a couple across generations.
    layout.relativeEdges.forEach((edge) => {
      const anchor = layout.positions.get(edge.anchor);
      const member = layout.positions.get(edge.member);
      if (!anchor || !member) return;
      let d: string;
      let labelX: number;
      let labelY: number;
      if (anchor.generation === member.generation) {
        const top = anchor.y - NODE_SIZE / 2;
        const lift = Math.min(40, 18 + 0.15 * Math.abs(member.x - anchor.x));
        d = `M ${anchor.x} ${top} C ${anchor.x} ${top - lift}, ${member.x} ${top - lift}, ${member.x} ${top}`;
        labelX = (anchor.x + member.x) / 2;
        labelY = top - lift * 0.75 - 3;
      } else {
        d = `M ${anchor.x} ${anchor.y} L ${member.x} ${member.y}`;
        labelX = (anchor.x + member.x) / 2;
        labelY = (anchor.y + member.y) / 2 - 3;
      }
      svg
        .append('path')
        .attr('data-pedigree-relative', `${edge.anchor}|${edge.member}`)
        .attr('d', d)
        .attr('fill', 'none')
        .attr('stroke', 'black')
        .attr('stroke-width', 1.4)
        .attr('stroke-linecap', 'round')
        .attr('stroke-dasharray', '0.1 4');
      svg
        .append('text')
        .attr('data-pedigree-relative-degree', `${edge.anchor}|${edge.member}`)
        .attr('x', labelX)
        .attr('y', labelY)
        .attr('text-anchor', 'middle')
        .attr('font-size', 10)
        .attr('font-weight', 600)
        .text('?')
        .append('title')
        .text(`${edge.member} is related through ${edge.anchor}, by an unknown degree`);
    });

    layout.familyUnits.forEach((unit) => {
      const parentPositions = unit.parents
        .map((parentId) => layout.positions.get(parentId))
        .filter((position): position is Position => Boolean(position));
      const childPositions = unit.children
        .map((childId) => layout.positions.get(childId))
        .filter((position): position is Position => Boolean(position))
        .sort((left, right) => left.x - right.x);
      if (!parentPositions.length || !childPositions.length) return;

      const centerX =
        average(parentPositions.map((position) => position.x)) ??
        parentPositions[0].x;
      const parentGenerations = new Set(
        parentPositions.map((position) => position.generation)
      );
      const sameGenerationParents = parentGenerations.size === 1;
      const maxParentBottomY = Math.max(
        ...parentPositions.map((position) => position.y + NODE_SIZE / 2)
      );
      const minChildTopY = Math.min(
        ...childPositions.map((position) => position.y - NODE_SIZE / 2)
      );
      let connectorY = maxParentBottomY + SIBLING_LINE_OFFSET;
      if (connectorY > minChildTopY - SIBLING_LINE_OFFSET) {
        connectorY = (maxParentBottomY + minChildTopY) / 2;
      }

      if (parentPositions.length >= 2 && sameGenerationParents) {
        appendLine(centerX, parentPositions[0].y, centerX, connectorY);
      } else {
        parentPositions.forEach((parentPosition) => {
          appendLine(
            parentPosition.x,
            parentPosition.y + NODE_SIZE / 2,
            parentPosition.x,
            connectorY
          );
          if (Math.abs(parentPosition.x - centerX) > 0.1) {
            appendLine(parentPosition.x, connectorY, centerX, connectorY);
          }
        });
      }

      if (childPositions.length === 1) {
        const childPosition = childPositions[0];
        if (Math.abs(centerX - childPosition.x) > 0.1) {
          appendLine(centerX, connectorY, childPosition.x, connectorY);
        }
        appendLine(
          childPosition.x,
          connectorY,
          childPosition.x,
          childPosition.y - NODE_SIZE / 2
        );
      } else {
        appendLine(
          childPositions[0].x,
          connectorY,
          childPositions[childPositions.length - 1].x,
          connectorY
        );
        childPositions.forEach((childPosition) => {
          appendLine(
            childPosition.x,
            connectorY,
            childPosition.x,
            childPosition.y - NODE_SIZE / 2
          );
        });
      }
    });

    layout.rows.forEach((row) => {
      const position = layout.positions.get(row.iid);
      if (!position) return;
      const member = layout.memberMap.get(row.iid);
      const rowSex = normalizedSexFor(row, member);
      const affected = isAffectedRow(row, member);
      const carrier = isCarrierStatus(member?.carrier_status);
      const hasPhenotypeAnnotation = phenotypeSampleSet.has(row.iid);
      const highlighted = highlightedSampleSet.has(row.iid);
      const xLinkedRecessiveFemaleCarrier =
        carrier && normalizedInheritance === 'XLR' && rowSex === '2';
      const qc = qcStatusBySample[row.iid];
      const qcMark = qc ? QC_MARKS[qc.status] : undefined;
      const fill = affected ? 'black' : 'white';
      const stroke = highlighted ? '#b91c1c' : 'black';
      const strokeWidth = highlighted ? 2.4 : 1;
      const generationIndex =
        layout.generationMembers[position.generation]?.indexOf(row.iid) ?? -1;
      const group = svg
        .append('g')
        .attr('data-pedigree-node', row.iid)
        .attr('data-generation', position.generation)
        .attr('data-generation-index', generationIndex)
        .attr('transform', `translate(${position.x}, ${position.y})`);
      if (qc) group.attr('data-qc-status', qc.status);
      // One tooltip for what the symbol's colours say: the QC reason, and the carrier type,
      // which the half-fill's colour alone would otherwise carry (#529).
      const tooltip = [
        qc ? qc.label || `QC: ${qc.status}` : null,
        carrier && !affected
          ? `Carrier${member?.carrier_type ? ` (${member.carrier_type})` : ''}`
          : null,
      ].filter(Boolean);
      if (tooltip.length) group.append('title').text(tooltip.join(' · '));

      const appendCarrierFill = () => {
        if (!carrier || affected) return;
        const cFill = carrierFillFor(member?.carrier_type);

        if (rowSex === '1') {
          // Male: draw left half of the square
          group
            .append('rect')
            .attr('x', -NODE_SIZE / 2)
            .attr('y', -NODE_SIZE / 2)
            .attr('width', NODE_SIZE / 2)
            .attr('height', NODE_SIZE)
            .attr('fill', cFill)
            .attr('stroke', 'none');
        } else if (rowSex === '2') {
          if (xLinkedRecessiveFemaleCarrier && !affected) {
            // XLR female: special convention is a dot in the middle
            group
              .append('circle')
              .attr('cx', 0)
              .attr('cy', 0)
              .attr('r', NODE_SIZE / 4)
              .attr('fill', cFill)
              .attr('stroke', 'none');
          } else {
            // Autosomal female: draw left semi-circle
            const r = NODE_SIZE / 2;
            const semiCirclePath = `M 0,${r} A ${r},${r} 0 0,1 0,${-r} Z`;
            group
              .append('path')
              .attr('d', semiCirclePath)
              .attr('fill', cFill)
              .attr('stroke', 'none');
          }
        } else {
          // Sex unknown: draw left half of the diamond
          const r = NODE_SIZE / 2;
          const halfDiamondPath = `M 0,${-r} L ${-r},0 L 0,${r} Z`;
          group
            .append('path')
            .attr('d', halfDiamondPath)
            .attr('fill', cFill)
            .attr('stroke', 'none');
        }
      };

      if (highlighted) {
        group
          .append('circle')
          .attr('data-phenotype-highlight', row.iid)
          .attr('cx', 0)
          .attr('cy', 0)
          .attr('r', NODE_SIZE / 2 + 8)
          .attr('fill', 'rgba(185, 28, 28, 0.12)')
          .attr('stroke', '#b91c1c')
          .attr('stroke-width', 1.6);
      }

      if (rowSex === '1') {
        group
          .append('rect')
          .attr('x', -NODE_SIZE / 2)
          .attr('y', -NODE_SIZE / 2)
          .attr('width', NODE_SIZE)
          .attr('height', NODE_SIZE)
          .attr('fill', fill)
          .attr('stroke', stroke)
          .attr('stroke-width', strokeWidth);
      } else if (rowSex === '2') {
        group
          .append('circle')
          .attr('cx', 0)
          .attr('cy', 0)
          .attr('r', NODE_SIZE / 2)
          .attr('fill', fill)
          .attr('stroke', stroke)
          .attr('stroke-width', strokeWidth);
      } else {
        const diamondPath =
          `M0 ${-NODE_SIZE / 2} ` +
          `L${NODE_SIZE / 2} 0 ` +
          `L0 ${NODE_SIZE / 2} ` +
          `L${-NODE_SIZE / 2} 0 Z`;
        group
          .append('path')
          .attr('d', diamondPath)
          .attr('fill', fill)
          .attr('stroke', stroke)
          .attr('stroke-width', strokeWidth);
      }

      appendCarrierFill();

      if (qc && qcMark) {
        // The ring follows the symbol's shape, QC_RING_OFFSET outside it.
        const half = NODE_SIZE / 2 + QC_RING_OFFSET;
        const ring = group.append<SVGElement>(
          rowSex === '1' ? 'rect' : rowSex === '2' ? 'circle' : 'path',
        );
        if (rowSex === '1') {
          ring.attr('x', -half).attr('y', -half).attr('width', half * 2).attr('height', half * 2);
        } else if (rowSex === '2') {
          ring.attr('cx', 0).attr('cy', 0).attr('r', half);
        } else {
          const tip = NODE_SIZE / 2 + QC_RING_OFFSET * Math.SQRT2;
          ring.attr('d', `M0 ${-tip} L${tip} 0 L0 ${tip} L${-tip} 0 Z`);
        }
        ring
          .attr('data-qc-ring', qc.status)
          .attr('fill', 'none')
          .attr('stroke', qcMark.color)
          .attr('stroke-width', qcMark.width)
          .attr('stroke-dasharray', qcMark.dash);
        const badge = group
          .append('g')
          .attr('data-qc-glyph', qc.status)
          .attr('transform', `translate(${-NODE_SIZE / 2 - 6}, ${-NODE_SIZE / 2 - 4})`);
        badge
          .append('circle')
          .attr('r', 5.5)
          .attr('fill', qcMark.color)
          .attr('stroke', 'white')
          .attr('stroke-width', 1.2);
        badge
          .append('text')
          .attr('text-anchor', 'middle')
          .attr('dominant-baseline', 'central')
          .attr('font-size', 8)
          .attr('font-weight', 700)
          .attr('fill', 'white')
          .text(qcMark.glyph);
      }

      if (hasPhenotypeAnnotation) {
        group
          .append('circle')
          .attr('data-phenotype-badge', row.iid)
          .attr('cx', NODE_SIZE / 2 + 6)
          .attr('cy', -NODE_SIZE / 2 - 4)
          .attr('r', 4.5)
          .attr('fill', '#dc2626')
          .attr('stroke', 'white')
          .attr('stroke-width', 1.4)
          .append('title')
          .text('HPO phenotype annotation');
      }

      group
        .append('text')
        .attr('data-pedigree-label', row.iid)
        .attr('x', 0)
        .attr('y', NODE_SIZE)
        .attr('text-anchor', 'middle')
        .attr('font-size', labelFontSize(member))
        .text(row.iid);
    });
  }, [layout, inheritanceModel, phenotypeSampleIds, highlightedSampleIds, qcStatusBySample]);

  return <svg ref={svgRef} className="pedigree-svg" role="img" aria-label={chartLabel} />;
};

// Memoized: the layout effect is expensive (D3 layout + full SVG clear/redraw),
// so skip re-rendering when callers pass referentially-stable props.
export default React.memo(Pedigree);
