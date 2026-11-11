import { describe, expect, it } from 'vitest';

import {
  SV_TYPE_ORDER,
  describeSvType,
  describeSvTypes,
  svKind,
  svTypeKey,
  svTypeLabel,
  type SvCopyChange,
  type SvTypeKey,
} from '../svTypes';

describe('describeSvTypes', () => {
  it('counts the drawn SVs by type, in track order', () => {
    expect(
      describeSvTypes([{ typeKey: 'DUP' }, { typeKey: 'DEL' }, { typeKey: 'DEL' }]),
    ).toBe('3 (2 DEL, 1 DUP)');
  });

  it('counts the SVs of the Other row as other, last', () => {
    expect(describeSvTypes([{ typeKey: 'OTHER' }, { typeKey: 'BND' }])).toBe('2 (1 BND, 1 other)');
  });

  it('keeps the row order the tracks draw, the Other row last', () => {
    expect(SV_TYPE_ORDER).toEqual(['DEL', 'DUP', 'INV', 'INS', 'BND', 'OTHER']);
    expect(SV_TYPE_ORDER.map(svTypeLabel)).toEqual(['DEL', 'DUP', 'INV', 'INS', 'BND', 'Other']);
  });
});

// The tracks draw every SV the list shows, not only DEL, DUP, INV, INS and BND by their exact
// names: a DEL:ME, a DUP:TANDEM, an INS:ME, a CNV and a TRA too. The server
// says which copies an SV changes (`copy_change`, by the rule of the SV ranking); the type
// without its subtypes says the rest.
describe('svTypeKey', () => {
  const rows: Array<[string | null | undefined, SvCopyChange | undefined, SvTypeKey]> = [
    ['DEL', 'loss', 'DEL'],
    ['DEL', undefined, 'DEL'],
    ['del', undefined, 'DEL'],
    ['DEL:ME', 'loss', 'DEL'],
    ['DEL:ME:ALU', undefined, 'DEL'],
    ['DUP', 'gain', 'DUP'],
    ['DUP:TANDEM', 'gain', 'DUP'],
    ['dup:tandem', undefined, 'DUP'],
    ['LOSS', 'loss', 'DEL'],
    ['CNV_GAIN', 'gain', 'DUP'],
    // A CNV names no direction: its carriers' copy numbers do, as the server read them.
    ['CNV', 'loss', 'DEL'],
    ['CNV', 'gain', 'DUP'],
    ['CNV', null, 'OTHER'],
    ['CNV', undefined, 'OTHER'],
    ['INV', undefined, 'INV'],
    ['INS', undefined, 'INS'],
    ['INS:ME', undefined, 'INS'],
    [' ins:me:line1 ', undefined, 'INS'],
    ['BND', undefined, 'BND'],
    ['TRA', undefined, 'BND'],
    ['tra', undefined, 'BND'],
    // Never dropped: a type none of the rows names is drawn in the Other row.
    ['CPX', undefined, 'OTHER'],
    ['SV', undefined, 'OTHER'],
    ['', undefined, 'OTHER'],
    [undefined, undefined, 'OTHER'],
  ];

  it.each(rows)('draws %j (copy change %j) in the %s row', (type, copyChange, row) => {
    expect(svTypeKey(type, copyChange)).toBe(row);
  });

  it('reads a type without its subtypes, as the server does', () => {
    expect(svKind('DEL:ME')).toBe('DEL');
    expect(svKind(' dup:tandem ')).toBe('DUP');
    expect(svKind(null)).toBe('');
  });
});

describe('describeSvType', () => {
  it('states a CNV with the copy-number change it is drawn by, and any other type as stored', () => {
    expect(describeSvType('CNV', 'loss')).toBe('CNV (copy-number loss)');
    expect(describeSvType('CNV', 'gain')).toBe('CNV (copy-number gain)');
    expect(describeSvType('CNV', undefined)).toBe('CNV (no copy-number change)');
    expect(describeSvType('DEL:ME', 'loss')).toBe('DEL:ME');
    expect(describeSvType('CPX')).toBe('CPX');
  });
});
