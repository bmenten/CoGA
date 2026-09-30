import { joinWithAnd } from '../../lib/format';

/**
 * How many variants of one kind the report asks for: the API's page maximum
 * (`MAX_VARIANT_PAGE_SIZE`). The report lists every reported variant up to it, and says so
 * when a list holds more rather than cutting it without a trace.
 */
export const REPORT_LIST_PAGE_SIZE = 10_000;

/** What a page of a reported-variant list says about its own completeness. */
export type ReportedListPage = {
  variants?: readonly unknown[] | null;
  total?: number | null;
  total_is_estimated?: boolean | null;
  /** The search behind the list read a capped candidate window. */
  candidates_capped?: boolean | null;
  /** How many candidates that window held. */
  candidate_limit?: number | null;
};

export type ReportedList = {
  /** What the list holds, in the plural: "small variants", "structural variants". */
  label: string;
  page: ReportedListPage | null | undefined;
};

/**
 * The notice a report shows, on screen and in print, when a list of reported variants is
 * not the whole list, or null when every list is complete. A list is incomplete when the
 * search behind it read a capped candidate window (a reported variant beyond it is
 * missing), or when it holds more variants than the page the report asked for.
 */
export function reportedListNotice(lists: ReportedList[]): string | null {
  const sentences: string[] = [];

  const capped = lists.filter(({ page }) => page?.candidates_capped);
  if (capped.length) {
    const limits = capped
      .map(({ page }) => page?.candidate_limit)
      .filter((limit): limit is number => typeof limit === 'number')
      .map((limit) => limit.toLocaleString());
    sentences.push(
      `Incomplete — the list of reported ${joinWithAnd(capped.map(({ label }) => label))} may miss a variant: ` +
        `the search behind it ${
          limits.length
            ? `stopped after the first ${joinWithAnd(limits)} candidates of the callset`
            : 'read only part of the callset'
        }, so a reported variant beyond that point is not shown.`,
    );
  }

  lists.forEach(({ label, page }) => {
    if (!page || page.candidates_capped) return;
    const shown = page.variants?.length ?? 0;
    if (page.total_is_estimated) {
      // The count stopped at its bound: there may be more than the page holds.
      sentences.push(
        `Incomplete — the report lists the first ${shown.toLocaleString()} reported ${label}; ` +
          'there may be more, and they are not shown.',
      );
      return;
    }
    if (typeof page.total === 'number' && shown < page.total) {
      sentences.push(
        `Incomplete — the report lists ${shown.toLocaleString()} of the ${page.total.toLocaleString()} ` +
          `reported ${label}; the others are not shown.`,
      );
    }
  });

  return sentences.length ? sentences.join(' ') : null;
}
