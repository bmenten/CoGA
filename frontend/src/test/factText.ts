/**
 * Matches a fact in a family page's header line (`FamilyFact`: its label, then its value) by
 * its whole text, "Showing 7": the label and the value are separate elements, which a plain
 * text query does not join.
 */
export const factText =
  (expected: string | RegExp) =>
  (_content: string, element: Element | null): boolean => {
    if (!element?.classList.contains('family-workspace-stat')) return false;
    const text = element.textContent ?? '';
    return typeof expected === 'string' ? text === expected : expected.test(text);
  };
