// The proband's parents for the de novo criteria (PM6/PS2) come from the pedigree's
// parent-child links, read as the backend's de novo mode reads them
// (clickhouse_variant_queries._parent_child_links). The member roles cannot tell a parent
// from a grandparent: a PED import stores everyone who has a child as 'father' or 'mother'.

import { describe, expect, it } from 'vitest';

import { parentageFromSampleQc, parentLinksFromRelationships, parentsOf } from '../pedigree';

describe('parentLinksFromRelationships', () => {
  it('reads a parent-child link with the parent on either side', () => {
    expect(
      parentLinksFromRelationships([
        { relationship_type: 'parent_child', sample_id_a: 'F', sample_id_b: 'P', role_a: 'father', role_b: 'child' },
        { relationship_type: 'parent_child', sample_id_a: 'P', sample_id_b: 'M', role_a: 'child', role_b: 'Mother' },
      ]),
    ).toEqual([
      { childId: 'P', parentId: 'F', role: 'father' },
      { childId: 'P', parentId: 'M', role: 'mother' },
    ]);
  });

  it('skips couples, a parent of unnamed role and incomplete links, as the backend does', () => {
    expect(
      parentLinksFromRelationships([
        { relationship_type: 'couple', sample_id_a: 'F', sample_id_b: 'M', role_a: 'partner', role_b: 'partner' },
        { relationship_type: 'parent_child', sample_id_a: 'X', sample_id_b: 'P', role_a: 'parent', role_b: 'child' },
        { relationship_type: 'parent_child', sample_id_a: '', sample_id_b: 'P', role_a: 'father', role_b: 'child' },
        { relationship_type: 'parent_child', sample_id_a: 'F', sample_id_b: 'P', role_a: 'father', role_b: null },
      ]),
    ).toEqual([]);
  });

  it('reads no links from a family without relationships', () => {
    expect(parentLinksFromRelationships(undefined)).toEqual([]);
    expect(parentLinksFromRelationships([])).toEqual([]);
  });
});

describe('parentsOf', () => {
  const links = [
    { childId: 'P', parentId: 'F', role: 'father' as const },
    { childId: 'P', parentId: 'M', role: 'mother' as const },
    { childId: 'F', parentId: 'GF', role: 'father' as const },
  ];

  it("names a child's own father and mother, not a grandparent", () => {
    expect(parentsOf('P', links)).toEqual({ father: 'F', mother: 'M' });
    expect(parentsOf('F', links)).toEqual({ father: 'GF', mother: undefined });
    expect(parentsOf('GF', links)).toEqual({ father: undefined, mother: undefined });
  });

  it('names no father when the pedigree gives the child two', () => {
    expect(parentsOf('P', [...links, { childId: 'P', parentId: 'X', role: 'father' }])).toEqual({
      father: undefined,
      mother: 'M',
    });
  });
});

// The Sample QC's parentage checks of the proband decide PS2 (confirmed) or PM6 (assumed).
describe('parentageFromSampleQc', () => {
  const pair = (a: string, b: string, status: string, inferred = 'parent-child', expected = 'parent-child') => ({
    sample_a: a,
    sample_b: b,
    expected_relationship: expected,
    inferred_relationship: inferred,
    status,
    message: `${a}-${b} ${status}`,
  });
  const mendel = (status: string) => ({ child: 'P', parents: ['F', 'M'], status, message: `Mendelian ${status}.` });

  it('confirms a parent whose parent-child check passes, with the child on either side', () => {
    expect(
      parentageFromSampleQc('P', {
        relatedness_checks: [pair('F', 'P', 'pass'), pair('P', 'M', 'pass'), pair('F', 'M', 'pass', 'unrelated', 'unrelated')],
        mendelian_checks: [mendel('pass')],
      }),
    ).toEqual({
      F: { status: 'confirmed', message: 'F-P pass' },
      M: { status: 'confirmed', message: 'P-M pass' },
    });
  });

  it('refutes a failed pair and leaves a warning unconfirmed', () => {
    expect(
      parentageFromSampleQc('P', {
        relatedness_checks: [pair('F', 'P', 'fail', 'unrelated'), pair('M', 'P', 'warn', 'indeterminate')],
      }),
    ).toEqual({
      F: { status: 'refuted', message: 'F-P fail' },
      M: { status: 'unconfirmed', message: 'M-P warn' },
    });
  });

  it('does not confirm a passing pair beside a Mendelian check that warns or fails, but does beside one that could not run', () => {
    const relatedness_checks = [pair('F', 'P', 'pass'), pair('M', 'P', 'pass')];
    for (const status of ['warn', 'fail']) {
      expect(parentageFromSampleQc('P', { relatedness_checks, mendelian_checks: [mendel(status)] }).F).toEqual({
        status: 'unconfirmed',
        message: `F-P pass Mendelian ${status}.`,
      });
    }
    expect(parentageFromSampleQc('P', { relatedness_checks, mendelian_checks: [mendel('skip')] }).F?.status).toBe('confirmed');
  });

  it("reads no parent from another member's pairs, a non-parent pair or no Sample QC", () => {
    expect(
      parentageFromSampleQc('P', {
        relatedness_checks: [pair('GF', 'F', 'pass'), pair('S', 'P', 'pass', 'sibling', 'sibling')],
      }),
    ).toEqual({});
    expect(parentageFromSampleQc('P', undefined)).toEqual({});
  });
});
