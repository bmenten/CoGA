import { describe, expect, it } from 'vitest';

import { REPORT_LIST_PAGE_SIZE, reportedListNotice } from '../reportedListCompleteness';

const rows = (count: number) => Array.from({ length: count }, (_, index) => ({ id: `v${index}` }));

describe('reportedListNotice', () => {
  it('asks for the API page maximum', () => {
    // MAX_VARIANT_PAGE_SIZE in the backend; a larger page_size is refused (422).
    expect(REPORT_LIST_PAGE_SIZE).toBe(10_000);
  });

  it('says nothing when every list is complete', () => {
    expect(
      reportedListNotice([
        { label: 'small variants', page: { variants: rows(2), total: 2 } },
        { label: 'structural variants', page: { variants: [], total: 0 } },
      ]),
    ).toBeNull();
  });

  it('says nothing for a list that has not loaded', () => {
    expect(reportedListNotice([{ label: 'small variants', page: undefined }])).toBeNull();
  });

  it('says when a list holds more reported variants than it shows', () => {
    expect(
      reportedListNotice([{ label: 'small variants', page: { variants: rows(3), total: 12_345 } }]),
    ).toBe('Incomplete — the report lists 3 of the 12,345 reported small variants; the others are not shown.');
  });

  it('says when the count stopped at its bound', () => {
    expect(
      reportedListNotice([
        { label: 'small variants', page: { variants: rows(4), total: 10_000, total_is_estimated: true } },
      ]),
    ).toBe('Incomplete — the report lists the first 4 reported small variants; there may be more, and they are not shown.');
  });

  it('says when the search behind a list read a capped candidate window', () => {
    expect(
      reportedListNotice([
        {
          label: 'structural variants',
          page: { variants: [], total: 0, total_is_estimated: true, candidates_capped: true, candidate_limit: 50_000 },
        },
      ]),
    ).toBe(
      'Incomplete — the list of reported structural variants may miss a variant: the search behind it stopped ' +
        'after the first 50,000 candidates of the callset, so a reported variant beyond that point is not shown.',
    );
  });

  it('names a capped window without its size when the size is not known', () => {
    expect(
      reportedListNotice([{ label: 'structural variants', page: { variants: [], candidates_capped: true } }]),
    ).toMatch(/the search behind it read only part of the callset/);
  });

  it('names every incomplete list', () => {
    const notice = reportedListNotice([
      { label: 'small variants', page: { variants: rows(1), total: 2 } },
      { label: 'structural variants', page: { variants: [], candidates_capped: true, candidate_limit: 50_000 } },
    ]);
    expect(notice).toMatch(/list of reported structural variants may miss a variant/);
    expect(notice).toMatch(/lists 1 of the 2 reported small variants/);
  });
});
