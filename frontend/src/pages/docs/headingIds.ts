// Heading ids for the in-app reference docs, so that a link to a heading of the same page
// (`[Phasing QC at the ROI](#phasing-qc-at-the-roi)`) jumps to it: without ids it went
// nowhere (ENG-19). The ids are the slugs GitHub gives the same Markdown, which the docs'
// links are written against (github-slugger's rule).

type HastNode = {
  type: string;
  tagName?: string;
  value?: string;
  properties?: Record<string, unknown>;
  children?: HastNode[];
};

// What a slug keeps: letters, combining marks, decimal digits, connector punctuation (`_`),
// spaces and hyphens. Every other character (punctuation, dashes, symbols) is dropped.
const DROPPED = /[^\p{Alphabetic}\p{M}\p{Nd}\p{Pc} -]/gu;

/**
 * A slugger for one document: the heading's text lowercased, without punctuation, each space a
 * hyphen ("The embryo call (at the ROI)" → `the-embryo-call-at-the-roi`). A slug already given
 * is numbered `-1`, `-2`, … as GitHub numbers a repeated heading.
 */
export const createHeadingSlugger = () => {
  const occurrences = new Map<string, number>();
  return (text: string): string => {
    const base = text.toLowerCase().replace(DROPPED, '').replace(/ /g, '-');
    let slug = base;
    while (occurrences.has(slug)) {
      const count = (occurrences.get(base) ?? 0) + 1;
      occurrences.set(base, count);
      slug = `${base}-${count}`;
    }
    occurrences.set(slug, 0);
    return slug;
  };
};

const textOf = (node: HastNode): string =>
  node.type === 'text' ? (node.value ?? '') : (node.children ?? []).map(textOf).join('');

/** A rehype plugin: each heading of the document without an id gets its slug. */
export const rehypeHeadingIds = () => (tree: HastNode) => {
  const slug = createHeadingSlugger();
  const visit = (node: HastNode) => {
    if (node.type === 'element' && /^h[1-6]$/.test(node.tagName ?? '') && !node.properties?.id) {
      node.properties = { ...node.properties, id: slug(textOf(node)) };
    }
    node.children?.forEach(visit);
  };
  visit(tree);
};
