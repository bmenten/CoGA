import { describe, expect, it } from 'vitest';

import { parsePedigree } from '../pedigree';

describe('parsePedigree', () => {
  it('reads whitespace-separated PED rows', () => {
    expect(parsePedigree('F1 PROBAND DAD MOM 2 2')).toEqual([
      { fid: 'F1', iid: 'PROBAND', pid: 'DAD', mid: 'MOM', sex: '2', phen: '2' },
    ]);
  });

  it('ignores blank lines and a missing pedigree', () => {
    expect(parsePedigree('F1 A 0 0 1 1\n\n  \nF1 B 0 0 2 1')).toHaveLength(2);
    expect(parsePedigree(null)).toEqual([]);
    expect(parsePedigree('')).toEqual([]);
  });
});
